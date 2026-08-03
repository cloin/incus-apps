# Application definition schema

An application file describes an Incus project and the services, networks, and
volumes that belong to it. Logical names are local to the application; the role
resolves them to project-scoped or namespaced Incus resources.

```yaml
application:
  name: example
  state: present
  description: Example mixed workload
  # project: optional-project-override
  defaults:
    storage_pool: local
    network: private
    profiles: []
    config:
      limits.cpu: "2"
      limits.memory: 2GiB

services:
  api:
    kind: application-container
    image: ghcr:owner/api:1.2.3
    root_size: 8GiB
    environment:
      DATABASE_URL: postgresql://postgres/example
    networks:
      - private
      - name: ingress
        # Optional; omit to use Incus DHCP/IPAM.
        ipv4_address: 10.210.250.20
    volumes:
      data: /srv/data
    depends_on:
      postgres:
        condition: healthy
    ingress:
      - host: "example.{{ lab_domain }}"
        port: 8080
        scheme: http
        network: ingress
    expose:
      - name: metrics
        protocol: tcp
        listen_address: 192.0.2.10
        listen_port: 9101
        target_port: 9100
    healthcheck:
      http: http://192.0.2.10:8080/health
      status: [200]
      interval: 3
      retries: 30

  worker:
    kind: system-container
    image: images:debian/13
    network_manager: systemd-networkd
    update:
      strategy: packages
      command: [sh, -lc, "apt-get update && apt-get -y dist-upgrade"]

  appliance:
    kind: virtual-machine
    image: images:debian/13/cloud
    root_size: 20GiB
    recreate_on_source_change: false
    config:
      limits.cpu: "4"
      limits.memory: 8GiB

networks:
  private:
    type: bridge
    config:
      ipv4.address: 10.210.10.1/24
      ipv4.nat: true
      ipv6.address: none

  ingress:
    external: true
    name: ingress

volumes:
  data:
    pool: local
    size: 20GiB
    retain: true
    config:
      snapshots.schedule: "@daily"
      snapshots.expiry: 7d
```

## Service fields

`kind` selects `application-container`, `system-container`, or
`virtual-machine`. `image`/`source` uses Incus remote notation; the built-in
aliases `docker:` and `ghcr:` select OCI registries, while `images:` selects the
Linux Containers image server.

`profiles`, `root_pool`, `root_size`, `target`, `ephemeral`, `config`, and
`devices` expose native Incus controls. A service can override application
defaults. `name` changes the physical instance name without changing the logical
dependency/DNS name used in the definition.

`networks` is a list of logical network names or attachment mappings. A string
such as `private` requests normal Incus DHCP. A mapping accepts `name` plus
optional `ipv4_address` and `ipv6_address` values. When `networks` is omitted,
the application default is attached. `volumes` maps logical volume names to
mount paths.

System-container images do not all configure additional NICs automatically.
Set `network_manager: systemd-networkd` when the image uses networkd and a
service has multiple attachments; the role installs a DHCP configuration for
all `eth*` interfaces. Application containers receive their network setup from
Incus and do not need this option.

`depends_on` accepts a list or mapping. The role performs a stable topological
sort and rejects unknown dependencies or cycles. Dependencies are reconciled and
health-checked before dependent services.

`environment` is converted to Incus `environment.*` keys and is hidden from
normal Ansible task output. Secret values should be Jinja references declared in
the application's `secrets` section. The runtime loads those variables from
OpenBao before it renders the complete catalog; never commit literal secret
values to Git.

## HTTP ingress

Any service may declare `ingress`; the role does not infer which services ought
to be public. Each entry has these fields:

- `host`: required FQDN, unique across the complete application catalog.
- `port`: required upstream service port.
- `scheme`: optional `http` (default) or `https` connection from Caddy to the
  service.
- `network`: optional logical network name, defaulting to `ingress`. The service
  must attach that network.

All HTTP routes share the Incus host's ports 80 and 443. Caddy selects the
upstream by hostname, so adding applications does not consume additional host
ports. An app can have multiple hostnames, and a private service such as a
database is allowed to declare an HTTP route if it actually speaks HTTP.
Non-HTTP database protocols belong under `expose` instead.

`group_vars/all/lab.yml` declares accepted ingress domains and their
certificate mode.
This permits gradual migrations in which some domains retain Caddy's internal
CA while a Cloudflare-managed domain uses DNS-01 certificates:

```yaml
incus_ingress_domains:
  - name: lab.example.net
    tls_mode: internal
  - name: lab.example.org
    tls_mode: cloudflare
```

Every route host must be below one of these domains. For the first bootstrap,
Cloudflare mode reads `INCUS_APPS_CLOUDFLARE_API_TOKEN` from the controller
environment and stores it in OpenBao. Semaphore receives it only at task
runtime through its read-only AppRole.

The ingress reconciliation runs after application reconciliation. It reads
`incus list --all-projects`, matches each route to the service's NIC on the
physical ingress network, and renders Caddy from that live IPv4 address. It
never assumes that a previous DHCP lease is still current.

### DHCP, static addresses, and IPAM

Omit address fields for the normal case. The managed Incus bridge supplies DHCP,
tracks the allocation, and registers it in its network state. Caddy does not
need a stable address because every run resolves the current allocation before
rendering its configuration.

For an external consumer that genuinely needs a stable address, declare it on
the attachment:

```yaml
networks:
  - name: ingress
    ipv4_address: 10.210.250.20
```

That becomes the NIC's Incus `ipv4.address`, keeping the reservation visible to
Incus DHCP/IPAM. Do not configure the same address manually inside the guest.
Catalog validation rejects duplicate explicit addresses on the same physical
network and addresses outside a managed network's subnet. This is what "stable
ingress IPs must not overlap" means: two declarative reservations cannot claim
the same layer-3 address. It does not mean each application needs a separate
subnet or that DHCP ranges from different, isolated networks cannot reuse
addresses.

## Raw TCP and UDP exposure

Use `expose` for SSH, databases, game servers, DNS, and other protocols that
cannot be routed by HTTP hostname:

```yaml
expose:
  - name: postgres
    protocol: tcp
    listen_address: 192.0.2.10
    listen_port: 5432
    target_address: 127.0.0.1
    target_port: 5432
```

`protocol` is `tcp` or `udp`. Addresses default to `0.0.0.0` and `127.0.0.1`.
Ports are always explicit: catalog validation rejects collisions for the same
protocol when addresses are equal or either listener is a wildcard. Explicit,
stable ports are preferable to random ports because clients and firewall rules
remain reproducible. Native `devices` remain available for advanced Incus proxy
options; a service cannot reuse a device name in both fields.

## Updates and replacement safety

`update.strategy: recreate` is the default for OCI application containers. The
update-all playbook removes and re-creates their root filesystems while retaining
attached custom volumes. System containers and VMs default to `packages` and run
the declared update command in place.

Changing the source of a system container or VM requires
`recreate_on_source_change: true`; this deliberate guard avoids destroying a
stateful OS instance because of an accidental image edit.

Volumes default to `retain: true`. Removing an application therefore preserves
data and leaves its project when necessary. Data deletion requires the explicit
`destroy_data=true` playbook option.

## OpenBao secrets

An application can declare one or more KV v2 groups without putting their
values in Git:

```yaml
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

`required` lists the Ansible variables that must exist before the catalog can
render. `generate` is optional and currently supports random strings between 16
and 256 characters. A generated key must also appear in `required`. Omit a key
from `generate` when an operator or external system must supply its value.

After adding a definition, prepare its secret group before the first GitOps
run:

```sh
scripts/run-playbook playbooks/prepare_app.yml -e app=example
```

Preparation creates only missing generated values and uses OpenBao KV
check-and-set semantics. It does not overwrite existing values. If a required
value has no generator, add it through OpenBao and run the command again.

Set `optional: true` only when the entire KV group may be absent. This is useful
for integrations such as optional DNS providers; it does not make individual
members of a present group optional.
