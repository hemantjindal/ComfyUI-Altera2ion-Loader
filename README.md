# ALTERA2ION Loader

Licensed encrypted LoRA loader for ALTERA2ION ComfyUI packages.

This repository contains the public ComfyUI custom node only. It does not include
ALTERA2ION LoRA files, decrypt keys, product packages, or customer assets.

## What It Does

- Loads ALTERA2ION `.a2enc` encrypted LoRA files.
- Starts the ALTERA2ION account activation flow when access is required.
- Verifies product ownership through `altera2ion.com`.
- Downloads the encrypted `.a2enc` file from ALTERA2ION when it is not already present locally.
- Decrypts the LoRA in memory before applying it to the model and CLIP.

## Installation

### ComfyUI Manager / Registry

After this node is published to the Comfy Registry, install it from ComfyUI
Manager by searching for:

```text
ALTERA2ION Loader
```

### Manual Install

Clone this repository into your ComfyUI custom nodes folder:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/hemantjindal/ComfyUI-Altera2ion-Loader.git
```

Install dependencies:

```bash
pip install -r ComfyUI-Altera2ion-Loader/requirements.txt
```

Restart ComfyUI.

## Usage

1. Purchase an ALTERA2ION package from `https://www.altera2ion.com`.
2. Open a workflow that uses **ALTERA2ION LoRA Loader**.
3. Select the encrypted `.a2enc` LoRA name in the loader node.
4. Queue the prompt.
5. Sign in on the ALTERA2ION activation page with the account that owns the product.
6. Approve the session and return to ComfyUI.

If the encrypted `.a2enc` file is already in `ComfyUI/models/loras/`, the node uses it.
If it is missing, the node downloads the encrypted `.a2enc` from ALTERA2ION after activation.

No license key is pasted into the node.

## Security Model

The custom node is public. The protected assets are the encrypted `.a2enc`
LoRA files and the server-side entitlement/decrypt-key flow.

The node is not useful without:

- a purchased ALTERA2ION product,
- a valid ALTERA2ION activation,
- a server-issued decrypt key.

## Cloud Support

Cloud-friendly session handling and gated `.a2enc` delivery are handled by
ALTERA2ION backend services. Raw `.safetensors` LoRA files are not distributed
by this repository.

## License

The custom node source code is MIT licensed. ALTERA2ION products, LoRAs,
workflows, guides, and generated package assets are not included in this repo
and remain governed by ALTERA2ION commercial terms.

## Support

Support: `support@altera2ion.com`
