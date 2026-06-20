# Colab launcher

This repository contains a one-cell Google Colab launcher for the CUDA seed tool.

## Open in Colab

After pushing this repository to GitHub, open:

```text
https://colab.research.google.com/github/YOUR_GITHUB_USERNAME/YOUR_REPO_NAME/blob/main/launch_colab.ipynb
```

In Colab, set:

```text
Runtime > Change runtime type > GPU
```

Then run the single notebook cell.

## Notebook options

The notebook exposes these fields:

```text
REPO_URL
BRANCH
LARGE_BIOMES
UNBOUND
PRINT_INTERVAL
CUDA_ARCH
RUN_ARGS
DISCORD_WEBHOOK_URL
DISCORD_MESSAGE_PREFIX
```

Common runtime arguments:

```text
--device 0
--device 0 --size 6000000
--device 0 --start 123456789
```

## Discord output forwarding

Seed hits are written by the program to an output file. When `DISCORD_WEBHOOK_URL` is set, `colab_run.sh` starts `discord_output_bridge.py`, which tails that output file and sends new seed-hit lines to the Discord webhook.

Recommended setup:

1. In Colab, open the Secrets panel.
2. Add a secret named:

```text
DISCORD_WEBHOOK_URL
```

3. Paste your Discord webhook URL as the value.
4. Leave the notebook's `DISCORD_WEBHOOK_URL` field blank.

You can also paste the webhook into the notebook field for quick testing, but do not commit real webhook URLs to a public GitHub repo.

You can also pass the webhook directly to the shell script as a parameter:

```bash
bash ./colab_run.sh --webhook "https://discord.com/api/webhooks/..." --device 0
```

Equivalent long name:

```bash
bash ./colab_run.sh --discord-webhook "https://discord.com/api/webhooks/..." --discord-prefix "Seed output" --device 0
```

Wrapper-only Discord parameters are removed before `./main` is launched. Other arguments, such as `--device 0`, are passed to `./main`.

Optional environment variables:

```text
DISCORD_OUTPUT_FILE=output.txt
DISCORD_MESSAGE_PREFIX="Seed output"
DISCORD_BATCH_LINES=10
DISCORD_BATCH_SECONDS=5
DISCORD_POLL_SECONDS=1
```

## Manual one-liner

```python
!rm -rf /content/comission_cuda && git clone --depth 1 https://github.com/YOUR_GITHUB_USERNAME/YOUR_REPO_NAME.git /content/comission_cuda && cd /content/comission_cuda/cuda_com && LARGE_BIOMES=0 UNBOUND=1 bash ./colab_run.sh --webhook "https://discord.com/api/webhooks/..." --device 0
```

For public notebooks, prefer Colab Secrets over embedding the webhook URL directly.
