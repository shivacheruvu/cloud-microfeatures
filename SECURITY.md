# Security policy

Every microfeature here is open source. Copy, fork and learn from them freely.
What is **never** shared is anything that lets someone spend money or act as me.

## Rules every project follows

1. **No credentials in code, ever.** API keys, service-account JSON, tokens and passwords live only in
   environment variables, GitHub Actions secrets, or GCP Secret Manager. Each repo ships a
   `.env.example` with placeholder values so anyone can plug in *their own* accounts.
2. **Keyless cloud auth where possible.** GitHub Actions authenticates to GCP with Workload Identity
   Federation (short-lived tokens), not downloaded service-account keys.
3. **Spend caps on every cloud project.** GCP budgets with alerts, and Databricks jobs sized to the
   smallest compute that works.
4. **Automated scanning.** Every push and pull request is scanned by gitleaks
   (`.github/workflows/secret-scan.yml`); a local pre-commit hook catches leaks before they leave the
   machine (`.pre-commit-config.yaml`).
5. **Nothing personal in public files.** No phone number, home address, or account IDs.

## If a secret ever leaks

1. Revoke / rotate the key immediately in the provider console (deleting the commit is not enough —
   the key is already public).
2. Then purge it from history (`git filter-repo`) and force-push.

## Reporting

Found something that looks like a live credential? Please open a GitHub issue titled
"Security" **without** pasting the value, and I will rotate it.
