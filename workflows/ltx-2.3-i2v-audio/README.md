# ltx-2.3-i2v-audio

Image-to-video with synchronized speech and a reference-voice identity anchor. Two
sampler passes (base + spatial upscaler refiner), AV latents kept together so video
and speech share noise schedules and stay in sync at decode time.

This document walks the graph node-by-node. For the high-level pipeline summary,
required custom nodes, and required model files, see the [top-level README](../../README.md).

---

## Graph topology

```
  1 UnetLoaderGGUF ── 2 LoraLoaderModelOnly ──┐
                                              │
  3 DualCLIPLoader ──┬─ 4 CLIPTextEncode (+) ─┤
                     └─ 5 CLIPTextEncode (−) ─┤
                                              ▼
  9 LoadAudio ─ 8 TrimAudioDuration ─► 10 LTXVReferenceAudio ─► 11 LTXVConditioning
                                              │                       │
  6 VAELoader (video) ────────────────────────┤                       │
  7 VAELoaderKJ (audio) ──────────────────────┘                       │
                                                                      │
  12 LoadImage ─ 13 ResizeImagesByLongerEdge ─ 14 LTXVPreprocess ──┐  │
  15 EmptyLTXVLatentVideo ──► 16 LTXVImgToVideoInplace ◄───────────┘  │
  17 LTXVEmptyLatentAudio ──► 18 LTXVConcatAVLatent ◄─ 16              │
                              │                                       │
                              ▼                                       ▼
                       19 LTXVScheduler           20 CFGGuider ◄──────┘
                              │                          │
                              └──────► 23 SamplerCustomAdvanced ◄── 21 RandomNoise, 22 KSamplerSelect
                                                │
                                                ▼
                                  24 LTXVSeparateAVLatent ─► (video, audio)
                                                │
                                  25 LTXVCropGuides
                                                │
                            ┌───────────────────┴──────────────────┐
                            ▼                                      ▼
        26 LatentUpscaleModelLoader                                │
        27 easy cleanGpuUsed                                       │
        28 LTXVLatentUpsampler ◄── 25 (video latent)               │
        29 LTXVImgToVideoInplace (re-inject frame, strength 1.0)   │
        30 LTXVConcatAVLatent ◄── 29 video + 24 audio ─────────────┘
                            │
                            ▼
        31 ManualSigmas ─► 35 SamplerCustomAdvanced ◄── 32 CFGGuider, 33 RandomNoise, 34 KSamplerSelect
                            │
                            ▼
                  36 LTXVSeparateAVLatent
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
  37 LTXVSpatioTemporalTiledVAEDecode  38 LTXVAudioVAEDecode
              │                           │
              └────────► 39 VHS_VideoCombine ◄────────┘
                                  │
                                  ▼
                              MP4 output
```

---

## Pertinent nodes

### Loaders (1, 2, 3, 6, 7, 26)

| ID | Node | Why it matters |
|----|------|----------------|
| `1` | `UnetLoaderGGUF` | Loads the 22B distilled LTX 2.3 UNet in GGUF Q4_0. Distilled checkpoint is why the base pass runs in **8 steps**. Swap to a heavier quant only if you also raise step count in node `19`. |
| `2` | `LoraLoaderModelOnly` | Identity LoRA (`id-lora-talkvid-ltx2.3`) that biases the talking-head generation toward consistent face/mouth shapes. `strength_model: 1.0` is the trained operating point — drop below 0.8 and lip sync degrades visibly. |
| `3` | `DualCLIPLoader` | Gemma 3 12B (fp4 mixed) + LTX 2.3 text projection. Pinned to **`device: "cpu"`** for portability — see the heads-up in the top-level README before switching to MultiGPU. |
| `6` | `VAELoader` | Video VAE used by the inplace conditioner (16, 29), the upsampler (28), and the final tiled decode (37). |
| `7` | `VAELoaderKJ` | Separate **audio** VAE — required by `LTXVReferenceAudio`, the empty audio latent, and the audio decode. Distinct VAE from the video one; do not cross the wires. |
| `26` | `LatentUpscaleModelLoader` | Loads the `ltx-2.3-spatial-upscaler-x2-1.1` model used only by the refiner pass. Fed through `easy cleanGpuUsed` (27) so VRAM is reclaimed between the base sampler and the upsampler. |

### Conditioning (4, 5, 10, 11)

| ID | Node | Why it matters |
|----|------|----------------|
| `4` | `CLIPTextEncode` (positive) | The prompt format is load-bearing. Three blocks: `[VISUAL]` shapes the scene, `[SPEECH]` is what the character actually says (drives lip motion), `[SOUNDS]` describes mic/voice character. Reordering or omitting `[SPEECH]` breaks lip sync. |
| `5` | `CLIPTextEncode` (negative) | Heavy on artifact suppression *and* speech-artifact terms (`lip flap`, `desynced audio`, `lip movement after line ends`). Keep the speech-domain negatives — they meaningfully reduce mouth flutter on silence frames. |
| `10` | `LTXVReferenceAudio` | The identity anchor. Wraps positive/negative conditioning with a reference-voice embedding. `identity_guidance_scale: 1.5` is a sweet spot — pushing to 2.0+ over-fits to the reference clip's prosody; below 1.0 the voice drifts. `start_percent`/`end_percent` of 0→1 means the anchor is applied across the full denoise. |
| `11` | `LTXVConditioning` | Stamps the **frame rate (25 fps)** onto the conditioning. Must match `frame_rate` in node `17` (audio latent) and node `39` (video combine) or audio and video will desync at mux time. |

### Image preparation (12, 13, 14)

| ID | Node | Why it matters |
|----|------|----------------|
| `12` | `LoadImage` | Starting frame. Aspect ratio should match node `15`'s base latent (default 832×480 → 16:9). |
| `13` | `ResizeImagesByLongerEdge` | Pre-conditions the frame at **1536 px long edge** so the upscaler pass has high-frequency detail to anchor to. Smaller values blur the final output even though the base pass is only 832×480. |
| `14` | `LTXVPreprocess` | `img_compression: 33` simulates JPEG-ish degradation on the conditioning frame so the model doesn't slavishly copy the source pixels into frame 0 — drop to 0 and you get a hard "photo → motion" pop on the first frame. |

### Audio reference (8, 9)

| ID | Node | Why it matters |
|----|------|----------------|
| `9` | `LoadAudio` | Voice clip used as identity reference. Any speaker, any line — only timbre is extracted. |
| `8` | `TrimAudioDuration` | Hard-trim to **20 s**. `LTXVReferenceAudio` ignores anything past ~20 s and longer clips just waste VRAM during the encode. Bypass this pair if you don't have a reference and the model will default to a neutral voice. |

### Base sampler pass (15–23)

| ID | Node | Why it matters |
|----|------|----------------|
| `15` | `EmptyLTXVLatentVideo` | Base-pass latent. **`length` must satisfy `8n + 1`** (e.g. 97, 105, 113) — LTX's temporal VAE compresses 8:1 + 1 boundary frame. Wrong length → shape mismatch at concat (node 18). |
| `17` | `LTXVEmptyLatentAudio` | Empty audio latent. **`frames_number` must equal node 15's `length`** and `frame_rate` must equal node 11's. Off-by-one here is the most common cause of "video plays but audio is silent." |
| `18` | `LTXVConcatAVLatent` | Glues video and audio latents along a shared channel so the sampler denoises them under one noise schedule — this is what keeps lips and speech locked together. |
| `19` | `LTXVScheduler` | **8 steps**, `max_shift: 2.05`, `base_shift: 0.95`, `stretch: true`, `terminal: 0.1`. Tuned for the distilled UNet — raising steps without a stronger checkpoint just costs time. |
| `20` | `CFGGuider` | **`cfg: 1.0`** — distilled LTX is trained to run at CFG 1 (no classifier-free guidance amplification). Raising CFG produces saturation/oversharp artifacts, not better adherence. |
| `21` | `RandomNoise` | Seed for the base pass. Match to node `33` for reproducibility. |
| `22` | `KSamplerSelect` | `euler`. Don't switch to a higher-order sampler — the scheduler in 19 is matched to euler's step semantics. |
| `23` | `SamplerCustomAdvanced` | Runs the base denoise. Output is the joint AV latent. |

### Upscale / refiner pass (24–36)

| ID | Node | Why it matters |
|----|------|----------------|
| `24` | `LTXVSeparateAVLatent` | Splits the joint latent so video can be upsampled while audio stays at base resolution. Outputs: `[0]` video, `[1]` audio. |
| `25` | `LTXVCropGuides` | Aligns the conditioning tensors to the post-sampler latent shape so the second pass can reuse them. Outputs `[0]` positive, `[1]` negative, `[2]` cropped video latent. |
| `28` | `LTXVLatentUpsampler` | **×2 spatial upscale in latent space.** This is why the final output is 1664×960 (832×480 × 2), not 832×480. Cheaper than decoding → upscaling pixels → re-encoding. |
| `29` | `LTXVImgToVideoInplace` (refiner) | Re-injects the starting frame at **`strength: 1.0`** so the upsampler doesn't drift the first frame off the user's input. Different strength than the base pass (0.6) — the refiner needs a harder pin. |
| `30` | `LTXVConcatAVLatent` | Rejoins the upsampled **video** with the *original-resolution* **audio** latent. Audio doesn't get upsampled; only the video resolution changes between passes. |
| `31` | `ManualSigmas` | **Hand-tuned 4-step refiner schedule**: `0.909375, 0.725, 0.421875, 0.0`. These specific sigmas are paired with `cfg: 1.0` and euler — substituting an `LTXVScheduler` here softens the output noticeably. |
| `32` | `CFGGuider` (refiner) | Notice this guider feeds from **`["10", 0]`**, i.e. the LoRA'd + reference-audio'd model — same conditioning path as the base pass, applied to the upsampled latent. |
| `33`–`35` | refiner noise / sampler / sampler-advanced | Same euler/CFG-1 setup as the base pass, 4 steps instead of 8. |
| `36` | `LTXVSeparateAVLatent` | Final split before the two VAEs decode their respective streams. |

### Decode & mux (37, 38, 39)

| ID | Node | Why it matters |
|----|------|----------------|
| `37` | `LTXVSpatioTemporalTiledVAEDecode` | Tiled decode (`spatial_tiles: 4`, `temporal_tile_length: 16`) so the full-resolution video latent decodes without OOM on a single card. `last_frame_fix: false` is fine for short clips; flip to `true` if you see a final-frame ghost on `length` ≥ ~145. |
| `38` | `LTXVAudioVAEDecode` | Decodes the speech latent to a waveform using the audio VAE from node `7`. |
| `39` | `VHS_VideoCombine` | Muxes images + audio into `socialreels/output_*.mp4` at 25 fps, H.264. `frame_rate` here must match nodes `11` and `17`. |

---

## Common edits

| You want to… | Touch these nodes |
|--------------|-------------------|
| Change spoken line | `4` (`[SPEECH]` block) |
| Change starting frame | `12` |
| Change voice identity | `9` (or bypass `8`+`10` for neutral voice) |
| Change clip length | `15` (`length`, must be `8n+1`) **and** `17` (`frames_number`) — keep them equal |
| Change base resolution / aspect | `15` (`width`, `height`) — final output is 2× these |
| Reproducible run | Set `21` and `33` to the same `noise_seed` |
| Change fps | `11`, `17`, `39` — all three |
| Filename / output path | `39` (`filename_prefix`) |
