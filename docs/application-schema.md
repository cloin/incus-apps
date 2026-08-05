# Application definitions

Files under `apps/` use the strict `cloin.incus-compose/v1` schema implemented
by the Git-pinned
[`cloin.incus_compose`](https://github.com/cloin/ansible-collection-incus-compose)
collection. The reusable collection owns Incus projects, networks, volumes,
OCI containers, system containers, virtual machines, dependency ordering,
route discovery, collision validation, and explicit cluster placement. This
repository keeps Caddy, OpenBao, Gitea, and Semaphore policy local.

Use [`examples/apps/linkwarden.yaml`](../examples/apps/linkwarden.yaml) as a
complete multi-service example. The collection's version-matched schema
reference is installed at
`ansible_collections/cloin/incus_compose/docs/application-schema.md` and is also
available in its Git repository.

## Project conventions

The bundled definitions use these collection fields:

- `type: oci`, `container`, or `vm` selects the Incus instance kind.
- `root: {pool, size}` configures the instance root disk.
- `routes` describes provider-neutral HTTP upstreams that this project renders
  into Caddy.
- `ports` creates explicit TCP/UDP Incus proxy devices for protocols that
  cannot be routed by hostname.
- `networks` and `volumes` refer to logical application resources.
- `placement.member` is required by the collection on an Incus cluster and is
  rejected on standalone Incus.

`incus_app_placement` in `group_vars/all/lab.yml` supplies placement to every
bundled definition. Keep it `{}` on standalone Incus. On a cluster, select one
online member:

```yaml
incus_app_placement:
  member: node1
```

All applications using the native shared `ingress` bridge must be co-located;
a node-local bridge does not span cluster members. The collection creates
managed networks through Incus's pending-member/finalize sequence and targets
custom volumes only when their storage driver is local. It omits placement for
shared storage.

## Routes, ports, and IPAM

Any service may declare a `routes` entry if it intentionally speaks HTTP. Caddy
shares host ports 80 and 443 and chooses an upstream by hostname, so normal
applications do not need unique external ports. `ports` is deliberately
open-ended with respect to workload type and is appropriate for SSH, database,
DNS, or game protocols. Catalog validation rejects conflicting listeners.

Omit a network attachment address to use Incus DHCP/IPAM. Caddy reconciliation
reads the live IPv4 allocation on every run, so a DHCP address need not remain
stable. An explicit `ipv4_address` becomes an Incus NIC reservation; validation
rejects duplicate addresses on the same physical network and addresses outside
a managed subnet.

## OpenBao extension metadata

The collection accepts namespaced `x-*` metadata but does not interpret it.
This project declares OpenBao requirements under `x-incus-apps`:

```yaml
x-incus-apps:
  secrets:
    - path: incus-apps/apps/example
      required:
        - example_database_password
        - example_api_key
      generate:
        example_database_password:
          type: random_string
          length: 48
```

`required` lists the Ansible variables needed to render the application.
`generate` is optional and supports random strings from 16 through 256
characters. Omit a key from `generate` when an operator or external system must
supply it. Set `optional: true` only when the entire KV group may be absent.

Prepare a new application's secret group before its first GitOps run:

```sh
scripts/run-playbook playbooks/prepare_app.yml -e app=example
```

Preparation creates only absent generated values with OpenBao KV check-and-set
semantics. It never overwrites existing values.

## Updates and removal

OCI services default to `update.strategy: recreate`; attached custom volumes
remain. System containers and VMs default to in-place package updates. Changing
the source of a system container or VM requires
`recreate_on_source_change: true`.

Custom volumes default to `retain: true`. Removing an application therefore
preserves its data and, when necessary, its project. The remove playbook only
deletes retained data when explicitly run with `-e destroy_data=true`.
