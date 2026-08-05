# Incus Apps

Incus Apps bootstraps an opinionated, GitOps-style application lab on an
existing Incus host. A new user clones this public template, supplies their lab
topology, and runs one playbook. The result is a private Gitea source of truth,
a Semaphore automation controller, OpenBao-backed runtime secrets, and shared
Caddy ingress for application definitions under `apps/`.

This is intended for a homelab or development lab on standalone Incus or an
existing Incus cluster. Clustered deployments deliberately co-locate the
bridge-backed stack on one member; this is not an HA OpenBao, database, or
production disaster-recovery design.

## What the bootstrap creates

Each platform component receives its own Incus project:

| Project | Workload | Purpose |
| --- | --- | --- |
| `ingress` | Caddy system container | Hostname-based HTTPS on host ports 80/443 |
| `openbao` | OpenBao system container | Application and controller secrets |
| `gitea` | Gitea OCI application container | Private Git source of truth |
| `semaphore` | Semaphore system container | Webhooks and Ansible execution |

Instances and volumes remain project-scoped. The projects share Incus networks
so their services can attach to one bridge named `ingress`; private bridges are
therefore globally namespaced from the application name. Caddy resolves current
Incus DHCP addresses during every reconciliation, so applications do not need
unique host ports or stable ingress addresses. Explicit TCP/UDP listeners
remain available for SSH, databases, game servers, and other non-HTTP
protocols; catalog validation rejects collisions.

The completed control loop is:

```text
git push -> Gitea webhook -> Semaphore Apply All -> OpenBao AppRole
                                      |
                                      +-> Incus API
```

Semaphore stores one OpenBao AppRole SecretID, not the lab's application
secrets. Each task exchanges it for a short-lived, read-only token, loads the
declared KV values into memory, renders the catalog, and reconciles Incus.

## Assumptions

- Incus is already installed, reachable, and initialized.
- The controller has an Incus client certificate already trusted by that host.
- The Incus host has a storage pool and can pull the configured images.
- The controller has Git, Python, `ansible-core` 2.20, and the Incus CLI.
- The target host and bundled binary downloads are x86-64.
- Ports 80, 443, 2222, 3001, and 3002 are free unless you change the two
  configurable bootstrap ports. Port 2222 is Gitea SSH.
- The default system-container image is Debian 13.
- Your LAN DNS can resolve `*.your-lab-domain` to the Incus host address.

Review pinned versions and checksums in the role defaults before deploying on a
different architecture or long after a release update.

## Quick start

### 1. Clone and install controller dependencies

```sh
git clone https://github.com/cloin/incus-apps.git
cd incus-apps
python3 -m pip install 'ansible-core>=2.20,<2.21' cryptography
ansible-galaxy collection install -r requirements.yml -p .ansible/collections
```

Use a virtual environment if your operating system manages its Python packages.

### 2. Create the lab configuration

```sh
cp config/lab.example.yml group_vars/all/lab.yml
```

Edit `group_vars/all/lab.yml` and replace every `CHANGE_ME`. This file contains
configuration, not credentials. Important values are:

- the Incus remote, HTTPS API URL, LAN address, and storage pool;
- an empty `incus_app_placement` for standalone Incus, or one online cluster
  member shared by the bundled bridge-backed applications;
- absolute paths to the controller's Incus client certificate, key, and pinned
  server certificate;
- an unused private `/16` prefix expressed as a quoted two-octet string, such
  as `"10.210"`;
- the LAN domain and TLS mode;
- administrator usernames/emails and the private Gitea repository name.

Typical Incus credential files live below `~/.config/incus/`, but place absolute
paths in the configuration. Verify access before bootstrap:

```sh
incus info REMOTE:
incus storage list REMOTE:
```

### 3. Configure LAN DNS

Create a wildcard A record or DNS rewrite for the selected lab domain pointing
to `incus_host_address`:

```text
*.lab.example.net -> 192.0.2.10
```

The name only needs to resolve on the LAN and through any VPN DNS configuration
you already use. A public A record is not required for the default internal TLS
mode.

The default endpoints are derived automatically:

- `https://git.<lab_domain>`
- `https://semaphore.<lab_domain>`
- `https://openbao.<lab_domain>`

### 4. Choose TLS mode

The default is Caddy's internal CA:

```yaml
incus_ingress_domains:
  - name: lab.example.net
    tls_mode: internal
```

Bootstrap installs this CA in Semaphore so GitOps works immediately. Browsers
and other clients must also trust Caddy's root. After deployment, pull it with:

```sh
incus file pull \
  REMOTE:caddy/var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt \
  ./incus-apps-caddy-root.crt --project ingress
```

Import that certificate as a trusted root on the client devices that need the
lab UI.

For a domain delegated to Cloudflare, set `tls_mode: cloudflare` and export a
scoped token for the first run:

```sh
export INCUS_APPS_CLOUDFLARE_API_TOKEN='...'
```

The token needs Zone/Zone/Read and Zone/DNS/Edit for the selected zone. It is
written to OpenBao and is not added to `lab.yml` or Semaphore as plaintext.
DNS-01 proves control through a temporary DNS TXT record; it is what allows a
publicly trusted wildcard certificate even when the application names resolve
only on your LAN.

### 5. Run the clean-room bootstrap

Start from a clean checkout containing only the intended `lab.yml` change. The
bootstrap will stage and commit the personalized checkout when it seeds Gitea.

```sh
scripts/run-playbook playbooks/bootstrap_lab.yml
```

The script asks you to choose an Ansible Vault password of at least 12
characters. The password is never written by this project: retain it in your
password manager. OpenBao generates its unseal key; the playbook encrypts that
key and a restricted operator login directly into
`recovery/openbao.vault.yml`, without first writing a plaintext recovery file.
For crash recovery, the initial root token exists only in that ciphertext while
bootstrap is incomplete. It is revoked and removed from the encrypted document
after the operator identity is verified.

The workflow then:

1. validates Incus connectivity, storage, certificates, domains, and ports;
2. deploys Caddy and OpenBao, initializes/unseals OpenBao, and generates
   platform credentials;
3. deploys Gitea and Semaphore and gives Semaphore its own Incus client
   certificate;
4. creates a private Gitea repository and pushes the personalized checkout;
5. configures Semaphore's inventory, environment, templates, and Gitea webhook;
6. launches a real AppRole-backed `Apply All` job;
7. revokes superseded bootstrap credentials and verifies all endpoints.

Bootstrap is state-aware and can be rerun with the same Vault password. It
stops rather than reinitializing OpenBao if the server state and encrypted
recovery file disagree.

## Resulting Git remotes

After bootstrap, the working checkout is reconfigured as:

```text
origin    https://git.<lab_domain>/<admin>/<repository>.git
upstream  original public template (fetch only; push URL is disabled)
```

The encrypted recovery file and personalized `lab.yml` are committed only to
the private Gitea origin. Neither file should be pushed to the public upstream.
To incorporate template updates:

```sh
git fetch upstream
git merge upstream/main
scripts/push
```

## Logging in

OpenBao first:

1. Open `https://openbao.<lab_domain>`.
2. Choose the username/password authentication method.
3. Use username `incus-apps-operator`.
4. From the private Gitea checkout created by bootstrap, display the generated
   operator password with:

   ```sh
   scripts/view-recovery
   ```

   A public template checkout intentionally has no recovery vault. If the
   encrypted file was restored elsewhere, pass its path explicitly as
   `scripts/view-recovery /path/to/openbao.vault.yml`.

The same encrypted document contains the unseal key. The operator policy can
manage only this project's KV data, policies, and AppRole; it is not a retained
root token.

In OpenBao's `kv` engine, the generated UI credentials are under:

- `incus-apps/gitea`
- `incus-apps/semaphore`

The configured usernames/emails come from `lab.yml`; their passwords are
generated during bootstrap. Caddy's optional DNS token is under
`incus-apps/ingress`.

## Adding an application

Application files live directly under `apps/`. Start with the Linkwarden
example:

```sh
cp examples/apps/linkwarden.yaml apps/linkwarden.yaml
```

Review its subnet, host listeners, images, volumes, and hostname. Each managed
private bridge needs a distinct third octet when using the default network
scheme. The application can declare values that OpenBao should generate:

```yaml
x-incus-apps:
  secrets:
    - path: incus-apps/apps/linkwarden
      required:
        - lab_linkwarden_postgres_password
        - lab_linkwarden_nextauth_secret
      generate:
        lab_linkwarden_postgres_password:
          type: random_string
          length: 48
        lab_linkwarden_nextauth_secret:
          type: random_string
          length: 64
```

Prepare those values before the first push:

```sh
scripts/run-playbook playbooks/prepare_app.yml -e app=linkwarden
git add apps/linkwarden.yaml
git commit -m 'Add Linkwarden'
scripts/push
```

`prepare_app.yml` only creates absent generated values. If a required value has
no generator, add it through OpenBao and rerun preparation. The push webhook
then launches Semaphore's `Apply All` template. Application secrets never enter
Git or the webhook payload. `scripts/push` retrieves the Gitea access token from
OpenBao for that one command and refuses to send it anywhere except the expected
private origin; it does not write the token to Git configuration or disk.

See [the application schema](docs/application-schema.md) for the strict
collection schema, containers versus VMs, cluster placement, volumes,
dependencies, health checks, DHCP/static IPAM, ports, routes, removal safety,
and this project's OpenBao extension.

## Day-two operations

Run these from the private checkout:

```sh
# Reconcile everything locally instead of waiting for a webhook
scripts/run-playbook playbooks/apply_all.yml

# Reconcile one application
scripts/run-playbook playbooks/apply.yml -e app=linkwarden

# Update non-platform workloads according to their update policy
scripts/run-playbook playbooks/update_all.yml

# Remove instances but retain volumes by default
scripts/run-playbook playbooks/remove.yml -e app=linkwarden

# Deliberately remove retained application data too
scripts/run-playbook playbooks/remove.yml -e app=linkwarden -e destroy_data=true

# Verify projects, APIs, OpenBao state, and HTTPS routes
scripts/run-playbook playbooks/verify.yml
```

Semaphore also exposes `Apply All` and `Update All` templates. `Apply All` is
the Gitea webhook target. `Update All` deliberately excludes the platform
applications so routine package updates cannot unexpectedly restart/seal
OpenBao or interrupt the GitOps control plane.

## Recovery and backups

OpenBao uses integrated Raft storage with one unseal share and a threshold of
one. A service restart seals it. Unseal it from a trusted private checkout:

```sh
scripts/run-playbook playbooks/unseal_openbao.yml
```

Back up all of the following off the Incus host:

- the private Gitea repository or at least `recovery/openbao.vault.yml` and
  `group_vars/all/lab.yml`;
- the Ansible Vault password, stored separately;
- Incus storage volumes/snapshots, especially OpenBao Raft and Gitea data;
- the controller's trusted Incus identity or a documented way to issue another.

The encrypted recovery file is not useful without its Vault password, and the
password cannot recover a missing file. Do not make Gitea the only copy: in a
full control-plane outage you may need the OpenBao recovery material before
Gitea is available.

The bootstrap intentionally refuses two dangerous ambiguous states:

- OpenBao initialized but no recovery vault in the checkout;
- a recovery vault present while OpenBao reports uninitialized.

Audit and restore the correct state rather than deleting either side to force a
new initialization.

## Contributing reusable changes

The private GitOps branch deliberately contains personalized configuration and
encrypted recovery material. Treat that ciphertext as sensitive and never use
the private branch as the source of a public pull request: removing a file in a
later commit does not remove it from the branch's earlier history.

Create public contributions from a clean branch rooted at the public upstream,
then reproduce or selectively apply only generic code, documentation, examples,
and tests:

```sh
git fetch upstream
git switch -c upstream-fix upstream/main
```

Before publishing, inspect the complete branch diff and history. Neither
`group_vars/all/lab.yml`, `recovery/openbao.vault.yml`, nor activated definitions
under `apps/` belong in the public template; reusable application definitions
belong under `examples/apps/`.

## Development checks

Static validation does not contact Incus:

```sh
python3 -m pip install pytest ansible-lint
pytest -q
python3 -m compileall -q action_plugins filter_plugins library
for playbook in playbooks/*.yml; do
  ansible-playbook --syntax-check "$playbook"
done
ansible-lint
```

The live bootstrap is intentionally the end-to-end test because it creates
projects, networks, volumes, instances, credentials, a private repository, and
webhooks. Always test it against a disposable Incus host before publishing a
release for other users.
