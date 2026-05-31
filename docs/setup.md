# Setup and Operations

## Server facts (as actually deployed)

- Proxmox host `pve`: Tailscale `100.124.47.47`, LAN reachable, NVIDIA driver 550.135 on host.
  (Note: the original spec listed `100.71.47.119` for pve. That address is a different
  machine. The real pve is `100.124.47.47`.)
- GPU: Quadro P2200, 5 GB, driver 550.135, CUDA 12.4. Uses the shared host `nvidia`
  kernel driver (not vfio), so LXCs can use it directly via device bind mounts.
- STELLA runs in LXC `114` named `stella` on the **management VLAN (VLAN 10)**, static IP
  `10.10.10.114/24`, gateway `10.10.10.1`. Reachable from the desktop over the Tailscale
  subnet router (which advertises `10.10.10.0/24`), ~30 ms.
  Host network is VLAN-aware (`vmbr0`, `bridge-vids 2-4094`); the mgmt interface is
  `vmbr0.10` at `10.10.10.5`. The other LXCs sit untagged on `192.168.1.0/24`.

## LXC 114 'stella' provisioning (already done)

Unprivileged Debian 12 LXC, 16 cores, 8 GB RAM, 30 GB rootfs on `llamazfs`,
`features: nesting=1,keyctl=1`. GPU passthrough lines appended to
`/etc/pve/lxc/114.conf` (copied from the existing docker LXC 111):

```
lxc.cgroup2.devices.allow: c 195:* rwm   # nvidia, nvidia-modeset, nvidiactl
lxc.cgroup2.devices.allow: c 236:* rwm   # nvidia-caps
lxc.cgroup2.devices.allow: c 509:* rwm   # nvidia-uvm
lxc.mount.entry: /dev/nvidia0 dev/nvidia0 none bind,optional,create=file
lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-modeset dev/nvidia-modeset none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm dev/nvidia-uvm none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm-tools dev/nvidia-uvm-tools none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-caps/nvidia-cap1 dev/nvidia-caps/nvidia-cap1 none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-caps/nvidia-cap2 dev/nvidia-caps/nvidia-cap2 none bind,optional,create=file
```

Inside the LXC:
- NVIDIA userland 550.135 installed from the `.run` with `--no-kernel-module --silent`
  (must match the host kernel module version exactly). Verify: `nvidia-smi`.
- Docker CE + `nvidia-container-toolkit`, configured with
  `nvidia-container-cli.no-cgroups=true` (required for unprivileged LXC).
  Verify: `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`.

## Ollama

Runs as a Docker container with GPU:

```
docker run -d --gpus all -v ollama:/root/.ollama -p 11434:11434 \
  --restart unless-stopped --name ollama ollama/ollama
docker exec ollama ollama pull llama3.2:3b
```

Check offload: `docker exec ollama ollama ps` should show `100% GPU`.

## STELLA service

- Code at `/opt/stella`, venv at `/opt/stella/.venv`.
- Piper binary at `/opt/piper/piper`, voice at `/opt/stella/server/voices/en_US-lessac-medium.onnx`.
- systemd unit `stella.service` runs `uvicorn server.main:app --host 0.0.0.0 --port 8420`.

Operate:
```
systemctl status stella          # state
journalctl -u stella -f          # logs
systemctl restart stella         # after editing code/config
```

Deploy updated code from the desktop repo:
```
tar czf - server config | ssh llama@100.124.47.47 \
  "sudo pct exec 114 -- tar xzf - --no-same-owner -C /opt/stella"
ssh llama@100.124.47.47 "sudo pct exec 114 -- systemctl restart stella"
```

## Test from the desktop

```
curl http://10.10.10.114:8420/health
curl -X POST http://10.10.10.114:8420/command \
  -H "Content-Type: application/json" -d "{\"text\":\"turn the lights on\"}"
```

## Customizing keybinds

Edit `config/keybinds.json` (intent -> key, plus `confirm_required` / `hold`), redeploy,
restart. Keys use pydirectinput names: `period`, `capslock`, `tab`, combos like `alt+y`.

## Temporary access note

A `/etc/sudoers.d/llama-nopasswd` rule was added on pve to allow hands-free provisioning.
Remove it when you no longer want passwordless sudo:
`sudo rm /etc/sudoers.d/llama-nopasswd`
