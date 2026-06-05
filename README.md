<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ComfyUI Workflows — Avocado Pty Ltd</title>
<style>
  :root {
    --bg: #0e0f12;
    --panel: #16181d;
    --border: #262931;
    --text: #e6e7ea;
    --muted: #9aa0aa;
    --accent: #8be9b4;
    --code-bg: #1c1f26;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    padding: 48px 24px;
  }
  main { max-width: 820px; margin: 0 auto; }
  h1 { font-size: 1.9rem; margin: 0 0 4px; }
  h2 { font-size: 1.25rem; margin: 36px 0 12px; padding-bottom: 6px; border-bottom: 1px solid var(--border); }
  h3 { font-size: 1.05rem; margin: 24px 0 8px; color: var(--accent); }
  p, li { color: var(--text); }
  .tagline { color: var(--muted); margin: 0 0 8px; }
  .badges { margin: 12px 0 24px; }
  .badge {
    display: inline-block; font-size: 0.78rem; padding: 3px 8px; margin-right: 6px;
    background: var(--panel); border: 1px solid var(--border); border-radius: 4px; color: var(--muted);
  }
  code, pre {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.88rem;
  }
  code { background: var(--code-bg); padding: 1px 5px; border-radius: 3px; color: #cfd5dd; }
  pre {
    background: var(--code-bg); padding: 14px 16px; border-radius: 6px;
    border: 1px solid var(--border); overflow-x: auto; line-height: 1.45;
  }
  pre code { background: none; padding: 0; }
  table { border-collapse: collapse; width: 100%; margin: 8px 0 16px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
  th { color: var(--muted); font-weight: 500; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.04em; }
  a { color: var(--accent); }
  ul { padding-left: 22px; }
  .callout {
    background: var(--panel); border: 1px solid var(--border); border-left: 3px solid var(--accent);
    padding: 12px 16px; border-radius: 4px; margin: 16px 0; color: var(--muted);
  }
  footer { margin-top: 56px; padding-top: 18px; border-top: 1px solid var(--border); color: var(--muted); font-size: 0.85rem; }
</style>
</head>
<body>
<main>

<h1>ComfyUI Workflows</h1>
<p class="tagline">Production-tested ComfyUI workflow graphs, open-sourced by <strong>Avocado Pty Ltd</strong>.</p>

<div class="badges">
  <span class="badge">ComfyUI</span>
  <span class="badge">LTX-Video 2.3</span>
  <span class="badge">API-format JSON</span>
  <span class="badge">MIT</span>
</div>

<p>This repository collects the ComfyUI workflows that power our internal video-generation
pipeline. Each file is the raw API-format graph that gets POSTed to <code>/prompt</code> on a
ComfyUI server — not the editor-format <code>.json</code> you drop on the canvas. Drop it into
a backend, swap inputs, and submit.</p>

<h2>Workflows</h2>
<table>
  <thead>
    <tr><th>File</th><th>Model</th><th>Purpose</th></tr>
  </thead>
  <tbody>
    <tr>
      <td><a href="workflows/ltx-2.3-i2v-audio.json"><code>workflows/ltx-2.3-i2v-audio.json</code></a></td>
      <td>LTX-Video 2.3 (22B distilled, GGUF Q4_0)</td>
      <td>Image-to-video with synchronized speech and a reference-voice identity anchor. Two-stage sampler with a spatial upscaler pass.</td>
    </tr>
  </tbody>
</table>

<h2>ltx-2.3-i2v-audio.json</h2>

<p>Image-to-video generation with talking-head lip sync. Given a starting frame and a
prompt containing <code>[VISUAL]</code> / <code>[SPEECH]</code> / <code>[SOUNDS]</code> blocks,
LTX 2.3 produces an MP4 with mouth movement matched to the spoken line. An optional
voice clip is fed through <code>LTXVReferenceAudio</code> as an identity anchor so the
generated voice stays consistent across clips of the same character.</p>

<h3>Pipeline at a glance</h3>
<ol>
  <li><strong>Loaders</strong> — UNet (GGUF), identity LoRA, dual CLIP (Gemma 3 12B + LTX text projection), video VAE, audio VAE.</li>
  <li><strong>Conditioning</strong> — positive/negative <code>CLIPTextEncode</code> → <code>LTXVReferenceAudio</code> (identity scale 1.5) → <code>LTXVConditioning</code> @ 25 fps.</li>
  <li><strong>Starting frame</strong> — <code>LoadImage</code> → resize to 1536px long edge → <code>LTXVPreprocess</code> (compression 33).</li>
  <li><strong>First pass</strong> — empty AV latent at base resolution → <code>SamplerCustomAdvanced</code> with <code>LTXVScheduler</code> (8 steps, euler, CFG 1.0).</li>
  <li><strong>Upscale pass</strong> — <code>LTXVLatentUpsampler</code> (×2 spatial) → re-inject starting frame at strength 1.0 → 4-step refiner with hand-tuned sigmas (<code>0.909375, 0.725, 0.421875, 0.0</code>).</li>
  <li><strong>Decode</strong> — tiled spatio-temporal VAE decode for video, audio VAE decode for the speech track, <code>VHS_VideoCombine</code> muxes them into an H.264 MP4.</li>
</ol>

<h3>Inputs you'll want to swap</h3>
<table>
  <thead><tr><th>Node</th><th>Field</th><th>Notes</th></tr></thead>
  <tbody>
    <tr><td><code>4</code> CLIPTextEncode (positive)</td><td><code>text</code></td><td>Use the three-block <code>[VISUAL]</code> / <code>[SPEECH]</code> / <code>[SOUNDS]</code> format. <code>[SPEECH]</code> drives lip motion.</td></tr>
    <tr><td><code>9</code> LoadAudio</td><td><code>audio</code></td><td>Voice identity reference (mp3/wav). Trimmed to 20 s by node <code>8</code>. Omit / bypass if you have no reference clip yet.</td></tr>
    <tr><td><code>12</code> LoadImage</td><td><code>image</code></td><td>Starting frame. Aspect ratio should match the empty latent in node <code>15</code>.</td></tr>
    <tr><td><code>15</code> EmptyLTXVLatentVideo</td><td><code>width</code>, <code>height</code>, <code>length</code></td><td>Frame count must be <code>8n + 1</code> (e.g. 97, 105, 113). Latent is base-pass resolution; final video is 2× spatial after the upscaler pass.</td></tr>
    <tr><td><code>21</code>, <code>33</code> RandomNoise</td><td><code>noise_seed</code></td><td>Set both to the same seed for reproducible runs.</td></tr>
    <tr><td><code>17</code> LTXVEmptyLatentAudio</td><td><code>frames_number</code></td><td>Match <code>length</code> in node <code>15</code>.</td></tr>
  </tbody>
</table>

<h3>Required custom nodes</h3>
<ul>
  <li><a href="https://github.com/Lightricks/ComfyUI-LTXVideo">ComfyUI-LTXVideo</a> — all <code>LTXV*</code> nodes.</li>
  <li><a href="https://github.com/city96/ComfyUI-GGUF">ComfyUI-GGUF</a> — <code>UnetLoaderGGUF</code>.</li>
  <li><a href="https://github.com/kijai/ComfyUI-KJNodes">ComfyUI-KJNodes</a> — <code>VAELoaderKJ</code>, <code>ResizeImagesByLongerEdge</code>, <code>ManualSigmas</code>, <code>TrimAudioDuration</code>.</li>
  <li><a href="https://github.com/yolain/ComfyUI-Easy-Use">ComfyUI-Easy-Use</a> — <code>easy cleanGpuUsed</code>.</li>
  <li><a href="https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite">ComfyUI-VideoHelperSuite</a> — <code>VHS_VideoCombine</code>.</li>
</ul>

<h3>Required model files</h3>
<table>
  <thead><tr><th>Slot</th><th>File</th><th>Folder</th></tr></thead>
  <tbody>
    <tr><td>UNet</td><td><code>ltx-2.3-22b-distilled-Q4_0.gguf</code></td><td><code>models/unet/</code></td></tr>
    <tr><td>Identity LoRA</td><td><code>id-lora-talkvid-ltx2.3.safetensors</code></td><td><code>models/loras/</code></td></tr>
    <tr><td>Text encoder 1</td><td><code>gemma_3_12B_it_fp4_mixed.safetensors</code></td><td><code>models/clip/</code></td></tr>
    <tr><td>Text encoder 2</td><td><code>ltx-2.3_text_projection_bf16.safetensors</code></td><td><code>models/clip/</code></td></tr>
    <tr><td>Video VAE</td><td><code>LTX23_video_vae_bf16.safetensors</code></td><td><code>models/vae/</code></td></tr>
    <tr><td>Audio VAE</td><td><code>LTX23_audio_vae_bf16.safetensors</code></td><td><code>models/vae/</code></td></tr>
    <tr><td>Spatial upscaler</td><td><code>ltx-2.3-spatial-upscaler-x2-1.1.safetensors</code></td><td><code>models/upscale_models/</code></td></tr>
  </tbody>
</table>

<h3>Submitting from a backend</h3>
<pre><code>POST /prompt
Content-Type: application/json

{
  "prompt": &lt;contents of workflows/ltx-2.3-i2v-audio.json&gt;,
  "client_id": "your-client-id"
}</code></pre>

<p>Reference images and audio must be uploaded to the server first via
<code>POST /upload/image</code> — then pass the returned filename into the
<code>LoadImage</code> and <code>LoadAudio</code> nodes.</p>

<div class="callout">
  <strong>Heads-up on text-encoder placement.</strong> The <code>DualCLIPLoader</code>
  in this file is pinned to <code>cpu</code> for portability. On a single-GPU box that
  is the right default — Gemma 3 12B in fp4 plus the 22B UNet on the same card will OOM.
  On a dual-GPU host, swap <code>DualCLIPLoader</code> for <code>DualCLIPLoaderMultiGPU</code>
  and set <code>device</code> to <code>cuda:1</code> to keep the main GPU free for the
  sampler — but only if ComfyUI was started with <code>--disable-dynamic-vram</code>,
  otherwise the allocator only registers <code>cuda:0</code> and the run will SIGSEGV
  the moment MultiGPU asks for a <code>cuda:1</code> context
  (see <a href="https://github.com/Comfy-Org/ComfyUI/issues/13792">Comfy-Org/ComfyUI#13792</a>).
</div>

<h2>License</h2>
<p>MIT. Use it, fork it, ship it. Attribution appreciated but not required.</p>

<h2>Contributing</h2>
<p>PRs welcome — particularly additional production-tested graphs (T2V, V2V, lip-sync,
upscaling). Keep workflows in API format and add a row to the table above describing
inputs and required custom nodes.</p>

<footer>
  © Avocado Pty Ltd — open-sourced as a contribution to the ComfyUI / open video-generation community.
</footer>

</main>
</body>
</html>
