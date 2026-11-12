# Moving an existing Turntable-1 install to Docker

The installer does it. On the machine that runs your current install:

```bash
curl -fsSL https://github.com/TorinML/turntable-1/releases/latest/download/install.sh | sh
```

When it finds the existing install, it asks whether to bring it across, then:

1. **Stops the old services** (it doesn't remove anything).
2. **Copies your data** from `/var/lib/turntable`: listening history, collection, covers, recordings and Beepnik.
3. **Imports your settings:** from `/etc/turntable` if you're on the unified config (v1.29 or later, including your admin password), or from the older `turntable-pet.toml`, `turntable-id.env` and service files.
4. **Keeps your album archive where it is,** if it was on `/mnt/turntable-nas`.
5. **Starts the new station** on the same port (8080), so your reverse proxy needs no changes.
6. **Offers to stop the old install starting at boot.**

**To go back** at any point:

```bash
cd ~/turntable-1 && docker compose down
sudo systemctl enable --now icecast2 'turntable-*'
```

Your old install's files are exactly as they were.

**While the image is private,** sign Docker in to GitHub before running the installer, with a token that can read packages:

```bash
echo YOUR_TOKEN | docker login ghcr.io -u TorinML --password-stdin
```
