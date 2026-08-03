# OpenBao recovery vault

`playbooks/bootstrap_lab.yml` creates `openbao.vault.yml` here. It contains the
OpenBao unseal key and restricted operator password encrypted with Ansible
Vault. Plaintext recovery data is never written to disk.

During an incomplete first bootstrap, the ciphertext can temporarily contain
the initialization root token so a failed run is recoverable. A successful run
verifies the restricted operator, revokes that root token, and rewrites this
file without it.

The encrypted file is committed only to the private Gitea repository created by
bootstrap. Keep an additional off-host copy. The Vault password must live in a
password manager outside this repository; losing either the file or password
makes the OpenBao data unrecoverable.
