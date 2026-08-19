# Gamma SSH teardown validation

This drill tests the `sky ssh down` current-context fix on disposable CPU machines before the wheel reaches production. It uses one isolated SkyPilot controller and two one-node SSH pools. No GPU is needed because the failure is in local kubeconfig teardown.

The two pool nodes matter. `gamma-pool-remove` is current when teardown starts, while `gamma-pool-keep` gives SkyPilot a valid context to select. A single pool tests only the no-context case and does not reproduce the production failure.

## Files in this directory

`pool_config.py` renders and validates the exact two-pool configuration. `validate_kubeconfig.py` checks the current-context invariant and prints non-secret JSON evidence. `run_gamma_drill.sh` runs one gated phase at a time. `ssh_node_pools.yaml.template` shows the generated YAML shape. Unit tests for the Python helpers are in `tests/unit_tests/test_gamma_ssh_down_tools.py`.

The scripts do not create or delete cloud VMs. Cloud IAM, networking, VM lifecycle, and cost controls belong to the environment owner. The drill only installs and removes k3s on the two hosts named in the gamma pool file.

## Topology and isolation

Use these three CPU VMs:

| VM | Role | Recommended minimum |
| --- | --- | --- |
| `gamma-deploy-api` | SkyPilot API server and kubeconfig owner | 4 vCPU, 16 GB RAM |
| `gamma-pool-keep` | Surviving SSH node pool | 2 vCPU, 8 GB RAM |
| `gamma-pool-remove` | Current SSH node pool removed by the drill | 2 vCPU, 8 GB RAM |

All three VMs must be in the same private network. The controller must reach both pool nodes over SSH. The SSH user needs passwordless sudo because `sky ssh up` installs k3s and `sky ssh down` removes it. Add an idle timeout and a cloud TTL or deletion ticket before creating the VMs.

Expose TCP port 46580 only to the engineer running the drill, or reach it through an SSH tunnel. Do not open the gamma API server to the public internet. The infrastructure page for this environment is `http://gamma-deploy-api:46580/dashboard/infra` when the private hostname resolves from the engineer's machine.

Gamma uses a new Linux user, home directory, Sky database, kubeconfig, SSH key, and API server. Do not copy `~/.sky`, `~/.kube`, the SSH pool registry, or database files from `prod-deploy-api`. The gamma kubeconfig may contain only names beginning with `ssh-gamma-`; the validator fails when it sees any other context, cluster, or user name.

The controller identity may administer only the two gamma pool VMs. It does not need access to production GKE, Lambda, Firestore, buckets, or the production Sky API. The pool nodes contain no production datasets or credentials.

The drill never runs `sky jobs cancel`, `sky jobs cancel-all`, `sky down`, or a cloud VM deletion command. It refuses pool names other than `gamma-pool-keep` and `gamma-pool-remove`. A gamma-only SSH private key, authorized only on the two disposable pool nodes, is the remote execution boundary.

## Stage 1: build the candidate wheel

Start from a clean SkyPilot checkout at the reviewed branch head. Record the commit before building.

```bash
git status --short
git rev-parse HEAD
FLOURISH_RELEASE_OUTPUT_DIR="$PWD/dist/gamma-ssh-down" ./build-flourish-release.sh
shasum -a 256 dist/gamma-ssh-down/*.whl
```

The commit, wheel filename, and SHA-256 form the artifact identity for the drill. Copy that exact wheel to `gamma-deploy-api`. Do not rebuild it on the VM.

## Stage 2: prepare the controller

Install Python 3.11, `uv`, `kubectl`, `jq`, `socat`, `netcat`, and an SSH client on `gamma-deploy-api`. Create a dedicated virtual environment and install the wheel with the SSH extra.

```bash
sudo mkdir -p /opt/gamma-sky
sudo chown "$(id -un)" /opt/gamma-sky
uv venv --python 3.11 /opt/gamma-sky/venv
candidate_wheel=/tmp/CANDIDATE_SKYPILOT_WHEEL.whl
uv pip install --python /opt/gamma-sky/venv/bin/python "$candidate_wheel[ssh]"
/opt/gamma-sky/venv/bin/sky --version
```

Create a gamma-only SSH key on the controller and authorize its public key on both pool nodes. Keep the private key on `gamma-deploy-api` with mode `0600`. Verify the exact connection SkyPilot will use before starting the API server:

```bash
mkdir -p /opt/gamma-sky/ssh
ssh-keygen -t ed25519 -N '' -f /opt/gamma-sky/ssh/gamma_ed25519
chmod 0600 /opt/gamma-sky/ssh/gamma_ed25519
ssh -i /opt/gamma-sky/ssh/gamma_ed25519 GAMMA_USER@GAMMA_KEEP_PRIVATE_IP true
ssh -i /opt/gamma-sky/ssh/gamma_ed25519 GAMMA_USER@GAMMA_REMOVE_PRIVATE_IP true
```

Create the gamma marker. The drill script refuses every phase without this exact file content.

```bash
mkdir -p ~/.sky ~/.kube
printf '%s\n' gamma-ssh-down-current-context > ~/.sky/gamma-environment
```

Start the API server from the gamma venv. Its natural home and kubeconfig belong to the dedicated gamma user.

```bash
/opt/gamma-sky/venv/bin/sky api start --deploy
/opt/gamma-sky/venv/bin/sky api info
```

## Stage 3: render the two-pool registry

Run this from the SkyPilot checkout copied to `gamma-deploy-api`:

```bash
/opt/gamma-sky/venv/bin/python tests/smoke_tests/ssh_node_pools/gamma/pool_config.py render \
  --keep-host GAMMA_KEEP_PRIVATE_IP \
  --remove-host GAMMA_REMOVE_PRIVATE_IP \
  --ssh-user GAMMA_USER \
  --identity-file /opt/gamma-sky/ssh/gamma_ed25519 \
  --output /opt/gamma-sky/ssh_node_pools.yaml
```

The renderer accepts only `gamma-pool-keep` and `gamma-pool-remove`. It also rejects a missing or group-readable private key.

Set the venv paths once for the shell that runs the drill:

```bash
export GAMMA_SKY_BIN=/opt/gamma-sky/venv/bin/sky
export GAMMA_PYTHON_BIN=/opt/gamma-sky/venv/bin/python
```

The examples below use these paths:

```bash
drill=tests/smoke_tests/ssh_node_pools/gamma/run_gamma_drill.sh
pools=/opt/gamma-sky/ssh_node_pools.yaml
kubeconfig="$HOME/.kube/config"
marker="$HOME/.sky/gamma-environment"
evidence=/opt/gamma-sky/evidence
```

## Stage 4: prove gamma is empty

Run the preflight and inspect both output files. Continue only when gamma has no jobs, clusters, or placements.

```bash
bash "$drill" preflight "$pools" "$kubeconfig" "$marker" "$evidence"
```

This is a fresh gamma database, so any listed job or cluster is unexpected. Stop and inspect it instead of deleting it through this drill.

## Stage 5: attach both CPU pools

```bash
bash "$drill" attach "$pools" "$kubeconfig" "$marker" "$evidence"
```

The phase runs `sky ssh up` for both pools, selects `ssh-gamma-pool-remove`, validates all three kubeconfig entry groups, and runs `sky check ssh`. Before teardown, record the two contexts and confirm that the remove context is current:

```bash
kubectl --kubeconfig "$kubeconfig" config current-context
kubectl --kubeconfig "$kubeconfig" config get-contexts -o name
```

## Stage 6: remove the current pool

```bash
bash "$drill" remove-current "$pools" "$kubeconfig" "$marker" "$evidence"
```

This is the primary acceptance test. It passes only when all of these conditions hold:

- `sky ssh down --infra gamma-pool-remove` returns zero;
- `current-context` becomes `ssh-gamma-pool-keep`;
- the removed context, cluster, and user entries are absent;
- the surviving context, cluster, and user entries remain;
- `sky check ssh` succeeds;
- `sky status` remains readable.

Open `http://gamma-deploy-api:46580/dashboard/infra` after the command and refresh the page. It must still show `gamma-pool-keep` and must not show `gamma-pool-remove`. Save a screenshot with the evidence directory.

If this phase fails, keep all three VMs. Copy the API server log, command logs, kubeconfig evidence, and `kubectl version --client` output before changing state.

## Stage 7: remove the last pool

After the dashboard check is saved, remove the survivor:

```bash
bash "$drill" remove-last "$pools" "$kubeconfig" "$marker" "$evidence"
```

This phase requires `current-context` to be unset and both gamma names to be absent from contexts, clusters, and users. An empty but loadable kubeconfig is the expected result.

## Stage 8: exercise concurrent teardown

Run the attach phase again, then submit both teardowns together:

```bash
bash "$drill" attach "$pools" "$kubeconfig" "$marker" "$evidence"
bash "$drill" concurrent-down "$pools" "$kubeconfig" "$marker" "$evidence"
```

The API server may execute both requests in separate long-request workers. The SkyPilot lock must serialize their kubeconfig mutations. Both commands must return zero, both pool entries must be gone, and `current-context` must be unset.

## Stage 9: inspect evidence and clean the VMs

The evidence directory contains command logs and non-secret kubeconfig summaries. It must include the candidate commit and wheel SHA-256 recorded in Stage 1, preflight job/status output, attach logs, before/after kubeconfig JSON, `sky check ssh`, `sky status`, concurrent teardown logs, and the dashboard result.

Confirm both pool VMs no longer have k3s installed. Then delete `gamma-pool-keep` and `gamma-pool-remove`. Stop the gamma API server, copy the evidence directory, and delete `gamma-deploy-api`. Verify the cloud inventory and billing view show no gamma VMs or disks.

Do not use the cleanup commands from this drill on production names. The scripts reject non-gamma pools and contexts, but the environment boundary remains the primary protection.

## Production release gate

Passing gamma proves the candidate source and wheel through the normal `sky ssh up/down` path. Production rollout still requires an immutable release tag and wheel asset, an independent SHA-256 download check, the pinned server and client locks, and the normal rollout review. Gamma state, kubeconfigs, SSH keys, and databases are never promoted.
