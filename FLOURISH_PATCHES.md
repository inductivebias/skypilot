# Flourish SkyPilot patches

This fork exists to make the exact SkyPilot code deployed by Flourish
reviewable and reproducible. Production must install an immutable commit from
`inductivebias/skypilot`; it must never depend on an uncommitted checkout or a
manual edit under `site-packages`.

## Repository contract

- Official upstream: `https://github.com/skypilot-org/skypilot`
- Flourish fork: `https://github.com/inductivebias/skypilot`
- Upstream release base: `v0.13.0`
- Upstream tag object: `77debbc66815e5adfe639f6e4d28c0c74afaf72b`
- Upstream peeled commit: `b1431e52d97c22e9bb8fa8b67f162543754ddaf5`
- Flourish branch: `flourish/kubernetes-401-refresh`
- Flourish release tag: `flourish-v0.13.0-kubernetes-401-refresh.1`
- Flourish source commit: `0b6f29fa6478d78054fac80218a5bcf0b8001ef1`
- Release wheel: `skypilot-0.13.0-py3-none-any.whl`
- Wheel SHA-256:
  `f4da74ec17ed33ec79adf7accffbba9697ea2f55445b7ffae23ee9af4d294ac6`

The release tag is the human-readable audit marker and identifies the reviewed
source commit. Consumers install the release wheel and hash-lock its exact
bytes, while recording the source commit and tag alongside that digest. The
deploy-api client and Sky API server pins must always move together. Never
replace a release asset or move a tag; publish a new numbered release instead.

## Build a release wheel

From a clean checkout of the reviewed release commit, use exactly one command:

```bash
./build-flourish-release.sh
```

Do not invoke `python -m build`, `pip wheel`, or `uv build` directly for a
Flourish release. SkyPilot's dashboard is a Next.js application whose source
is not sufficient at runtime. The release command runs `npm ci` and
`npm run build` first, verifies that `sky/dashboard/out/index.html` exists,
builds the wheel in a temporary directory, verifies that the compiled index is
inside the wheel, and prints the final wheel path and SHA-256. The artifact is
written under `dist/flourish-release/` by default; set
`FLOURISH_RELEASE_OUTPUT_DIR` only when a release process needs another
destination.

The same command can verify an existing artifact without rebuilding it:

```bash
./build-flourish-release.sh --verify-only path/to/skypilot-0.13.0-py3-none-any.whl
```

This payload check is a release gate, not an optional UI test. The Sky API can
remain healthy and schedule jobs while `/dashboard/` returns HTTP 500 if the
compiled files are absent. After publishing, install the hash-locked wheel in
the Flourish local server rehearsal; that rehearsal must receive HTTP 2xx from
`http://127.0.0.1:46580/dashboard/` before production deployment.

## `flourish-v0.13.0-kubernetes-401-refresh.1`

The `.1` wheel is retained as incident evidence but must not be deployed. It
was built without the required dashboard compilation step and therefore lacks
`sky/dashboard/out/index.html`. Production rejected the rollout on 2026-08-17
when `/dashboard/` returned HTTP 500, and rolled back to the upstream 0.13.0
environment. Never replace the `.1` asset or move its tag; publish `.2` from a
reviewed commit using the one-command builder above.

### Why this patch exists

Flourish attaches Lambda Cloud machines to SkyPilot as SSH node pools backed
by k3s. A stable logical slot, such as `deploy-lambda-2`, can be replaced by a
new rental. The replacement has a new Kubernetes CA and admin client
certificate, while its kubeconfig context intentionally retains the stable
name `ssh-deploy-lambda-2`.

SkyPilot 0.13.0 keeps generated Kubernetes clients in long-lived managed-job
controller processes. A controller that used a previous incarnation can
therefore send the old certificate to the replacement cluster. Fresh
`kubectl` and freshly constructed Python clients work, but the cached client
receives:

```text
kubernetes.client.exceptions.ApiException: (401)
Reason: Unauthorized
```

The failure was observed in production on 2026-08-17 UTC. Sky jobs 241, 251,
and 252 created or retained four-GPU pods on `deploy-lambda-2` while their
controllers could not query or terminate those pods. Ownership-verified
cleanup released the resources. The defect is in the controller's in-memory
client cache, not in Lambda, k3s, the kubeconfig exec plugin, workload IAM, or
the job container.

### Behavior added

`sky/adaptors/kubernetes.py::RetryableClientWrapper` now treats an
authentication rejection as a credential-incarnation boundary:

1. Normal interval refresh runs first.
2. The wrapper records the client generation used for the Kubernetes method.
3. Only `ApiException(status=401)` enters recovery; 403 and every other error
   retain their existing behavior.
4. Under the existing refresh lock, the wrapper reconstructs the client from
   the original getter, context, positional arguments, and keyword arguments.
5. If another thread already replaced that generation, the caller reuses the
   new client instead of constructing another one.
6. The displaced `ApiClient` is closed.
7. The original method is retried exactly once with identical arguments.
8. Any retry failure is propagated; there is no unbounded authentication
   loop.

Every detected 401 emits one structured warning with the context, method,
refresh outcome, retry result, and this canonical alarm signature:

```text
kubernetes.client.exceptions.ApiException: (401)
```

`retry_succeeded=true` identifies an automatically recovered call.
`retry_succeeded=false` identifies a call that still needs operator attention.

### Tests

The Kubernetes adaptor unit suite covers:

- one 401 recreates the client and retries successfully;
- a second 401 is propagated after one retry;
- non-401 errors never recreate or retry;
- concurrent failures against one generation create one replacement client;
- context/getter arguments are preserved and the old client is closed;
- interval refresh rereads a real temporary kubeconfig exec credential from
  disk and observes a replaced client-certificate fingerprint;
- all pre-existing client cleanup, interval, and context-isolation behavior.

Local verification:

```bash
cd ~/Work/repo/skypilot
.venv/bin/pytest -q -n 0 \
  tests/unit_tests/test_sky/adaptors/test_kubernetes_adaptor.py
```

No test uses a live Kubernetes cluster or cloud account.

### Flourish integration

The Flourish repository records this release wheel in both dependency inputs
and hash-locks it in both generated lock files:

```text
infra/deploy/api/server/requirements.in
infra/deploy/api/server/requirements.txt
infra/deploy/api/deploy_api/requirements.in
infra/deploy/api/deploy_api/requirements.txt
```

The server runtime must also set
`SKYPILOT_KUBECONFIG_REFRESH_INTERVAL_SECONDS=300`. Periodic recreation limits
the lifetime of otherwise idle cached clients; immediate 401 recovery closes
the replacement race. Neither mechanism is a substitute for the other.

Build and deployment must use the supported Flourish setup path. Do not copy
this file into a live virtualenv and do not patch a production installation by
hand.

The wheel is published on the GitHub release for the tag. GitHub-generated
source archives are deliberately not used as install artifacts because their
generated gzip bytes do not provide a stable single hash for
`uv --require-hashes` across fetches.

### Rollback and removal

Rollback pins both Flourish components to the previous immutable SkyPilot
commit and rebuilds them. It does not mutate Sky's database, kubeconfig, slot
ledger, or any running workload.

Remove this fork patch only after an upstream SkyPilot release contains
equivalent generation-fenced, one-time 401 recovery and the Flourish
same-context replacement drill passes against that release. Record the
upstream PR/release here before deleting this section.
