#!/usr/bin/env python3
"""Convert a ComfyUI API-format ("prompt") graph into an editor-format
("workflow") graph that can be dropped onto the ComfyUI canvas.

The API format is the flat ``{id: {class_type, inputs}}`` dict POSTed to
``/prompt``. It carries the graph topology and every input value, but none of
the things the canvas needs: node positions, slot definitions, link records,
or the ordered ``widgets_values`` array. This script reconstructs those.

Usage:
    python tools/api-to-editor.py INPUT_api.json OUTPUT_workflow.json

Connections are reproduced exactly (source output-slot indices come straight
from the API graph; input-slot indices are kept internally consistent with the
link records). Widget values are emitted in the order they appear in each
node's ``inputs`` block, which matches the node's INPUT_TYPES declaration order
for a normally-authored graph. Slot types are looked up in OUTPUTS below so the
canvas colours wires correctly; an unknown node still converts, it just gets a
generic ``*`` slot type. After loading, sanity-check widget values on any
custom node not listed in OUTPUTS.
"""
import json
import sys
from collections import defaultdict

# Output slots per node class, as (slot_name, slot_type), in slot order.
# Only the first len() slots are emitted; extra real slots are harmless because
# the API graph only ever references the indices it actually uses.
OUTPUTS = {
    "UnetLoaderGGUF": [("MODEL", "MODEL")],
    "LoraLoaderModelOnly": [("MODEL", "MODEL")],
    "DualCLIPLoader": [("CLIP", "CLIP")],
    "CLIPTextEncode": [("CONDITIONING", "CONDITIONING")],
    "VAELoader": [("VAE", "VAE")],
    "VAELoaderKJ": [("VAE", "VAE")],
    "LoadAudio": [("AUDIO", "AUDIO")],
    "TrimAudioDuration": [("AUDIO", "AUDIO")],
    "LTXVReferenceAudio": [("model", "MODEL"), ("positive", "CONDITIONING"), ("negative", "CONDITIONING")],
    "LTXVConditioning": [("positive", "CONDITIONING"), ("negative", "CONDITIONING")],
    "LoadImage": [("IMAGE", "IMAGE"), ("MASK", "MASK")],
    "ResizeImagesByLongerEdge": [("IMAGE", "IMAGE")],
    "LTXVPreprocess": [("IMAGE", "IMAGE")],
    "EmptyLTXVLatentVideo": [("LATENT", "LATENT")],
    "LTXVImgToVideoInplace": [("LATENT", "LATENT")],
    "LTXVEmptyLatentAudio": [("LATENT", "LATENT")],
    "LTXVConcatAVLatent": [("LATENT", "LATENT")],
    "LTXVScheduler": [("SIGMAS", "SIGMAS")],
    "CFGGuider": [("GUIDER", "GUIDER")],
    "RandomNoise": [("NOISE", "NOISE")],
    "KSamplerSelect": [("SAMPLER", "SAMPLER")],
    "SamplerCustomAdvanced": [("output", "LATENT"), ("denoised_output", "LATENT")],
    "LTXVSeparateAVLatent": [("video", "LATENT"), ("audio", "LATENT")],
    "LTXVCropGuides": [("positive", "CONDITIONING"), ("negative", "CONDITIONING"), ("latent", "LATENT")],
    "LatentUpscaleModelLoader": [("UPSCALE_MODEL", "UPSCALE_MODEL")],
    "easy cleanGpuUsed": [("anything", "*")],
    "LTXVLatentUpsampler": [("LATENT", "LATENT")],
    "ManualSigmas": [("SIGMAS", "SIGMAS")],
    "LTXVSpatioTemporalTiledVAEDecode": [("IMAGE", "IMAGE")],
    "LTXVAudioVAEDecode": [("AUDIO", "AUDIO")],
    "VHS_VideoCombine": [("Filenames", "VHS_FILENAMES")],
}

# Widgets that the frontend expands with a trailing "control_after_generate"
# entry. We pin them to "fixed" so a dropped graph reproduces by default.
SEED_WIDGETS = {"noise_seed", "seed", "rand_seed"}


def is_link(value):
    """An API input value of the form [node_id, slot_index] is a connection."""
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], (str, int))
        and isinstance(value[1], int)
    )


def output_slot(class_type, index):
    slots = OUTPUTS.get(class_type)
    if slots and index < len(slots):
        return slots[index]
    return (f"out{index}", "*")


def convert(api):
    ids = list(api.keys())
    # Reuse the original numeric ids as canvas ids when they're already unique
    # integers, so canvas node numbers line up with the API graph and any docs
    # that reference them. Otherwise fall back to a 1..N renumbering.
    if all(nid.lstrip("-").isdigit() for nid in ids) and len(set(ids)) == len(ids):
        int_id = {nid: int(nid) for nid in ids}
    else:
        int_id = {nid: i + 1 for i, nid in enumerate(ids)}

    nodes = {}
    for nid in ids:
        cls = api[nid]["class_type"]
        inputs = api[nid].get("inputs", {})

        slot_inputs = []   # (name, type, src_node, src_slot) — filled after pass 2
        widget_values = []
        link_inputs = []   # (name, src_id, src_slot)
        for name, val in inputs.items():
            if is_link(val):
                link_inputs.append((name, str(val[0]), val[1]))
            else:
                widget_values.append(val)
                if name in SEED_WIDGETS:
                    widget_values.append("fixed")

        nodes[nid] = {
            "cls": cls,
            "link_inputs": link_inputs,
            "widget_values": widget_values,
            "inputs": [],
            "outputs": defaultdict(list),  # slot_index -> [link_ids]
        }

    # Build link records and wire input slots.
    links = []  # [link_id, src_int, src_slot, dst_int, dst_slot, type]
    link_id = 0
    for nid in ids:
        node = nodes[nid]
        for slot_index, (name, src_id, src_slot) in enumerate(node["link_inputs"]):
            link_id += 1
            _, stype = output_slot(nodes[src_id]["cls"], src_slot)
            links.append([link_id, int_id[src_id], src_slot, int_id[nid], slot_index, stype])
            node["inputs"].append({"name": name, "type": stype, "link": link_id})
            nodes[src_id]["outputs"][src_slot].append(link_id)

    order = topo_order(ids, nodes)
    layers = layer_assignment(ids, nodes)

    out_nodes = []
    per_layer = defaultdict(int)
    for nid in ids:
        node = nodes[nid]
        cls = node["cls"]
        layer = layers[nid]
        row = per_layer[layer]
        per_layer[layer] += 1

        n_out = max([len(OUTPUTS.get(cls, []))] + [k + 1 for k in node["outputs"]])
        outputs = []
        for s in range(n_out):
            sname, stype = output_slot(cls, s)
            outputs.append({
                "name": sname,
                "type": stype,
                "links": node["outputs"].get(s, []) or None,
                "slot_index": s,
            })

        out_nodes.append({
            "id": int_id[nid],
            "type": cls,
            "pos": [layer * 360 + 40, row * 220 + 40],
            "size": [300, 120],
            "flags": {},
            "order": order[nid],
            "mode": 0,
            "inputs": node["inputs"],
            "outputs": outputs,
            "properties": {"Node name for S&R": cls},
            "widgets_values": node["widget_values"],
        })

    out_nodes.sort(key=lambda n: n["id"])
    return {
        "last_node_id": max(int_id.values()),
        "last_link_id": link_id,
        "nodes": out_nodes,
        "links": links,
        "groups": [],
        "config": {},
        "extra": {},
        "version": 0.4,
    }


def topo_order(ids, nodes):
    """Execution order index per node (Kahn's algorithm)."""
    deps = {nid: {src for _, src, _ in nodes[nid]["link_inputs"]} for nid in ids}
    order = {}
    placed = set()
    idx = 0
    remaining = set(ids)
    while remaining:
        ready = [nid for nid in ids if nid in remaining and deps[nid] <= placed]
        if not ready:  # cycle guard — should not happen for a valid graph
            ready = [next(iter(remaining))]
        for nid in ready:
            order[nid] = idx
            idx += 1
            placed.add(nid)
            remaining.discard(nid)
    return order


def layer_assignment(ids, nodes):
    """Longest-path layering so wires generally flow left-to-right."""
    deps = {nid: [src for _, src, _ in nodes[nid]["link_inputs"]] for nid in ids}
    layer = {}

    def depth(nid, stack):
        if nid in layer:
            return layer[nid]
        if nid in stack:  # cycle guard
            return 0
        stack.add(nid)
        d = 0 if not deps[nid] else 1 + max(depth(p, stack) for p in deps[nid])
        stack.discard(nid)
        layer[nid] = d
        return d

    for nid in ids:
        depth(nid, set())
    return layer


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: api-to-editor.py INPUT_api.json OUTPUT_workflow.json")
    with open(sys.argv[1]) as f:
        api = json.load(f)
    workflow = convert(api)
    with open(sys.argv[2], "w") as f:
        json.dump(workflow, f, indent=2)
        f.write("\n")
    print(f"wrote {sys.argv[2]}: {len(workflow['nodes'])} nodes, "
          f"{len(workflow['links'])} links")


if __name__ == "__main__":
    main()
