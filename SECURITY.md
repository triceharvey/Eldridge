# Security Policy

Eldridge treats AI providers and agents as untrusted service identities. A report that could
allow an agent, provider, webhook, or repository task to bypass policy, authorization,
isolation, revision binding, audit integrity, or human approval is a security issue.

## Reporting a vulnerability

Do not disclose suspected vulnerabilities in a public issue, discussion, pull request, or
commit. Use GitHub's private vulnerability reporting for this repository. If that feature is
temporarily unavailable, contact the repository owner privately through the contact method on
their GitHub profile and include only enough information to establish a secure reporting
channel.

Include the affected revision, component, expected security boundary, observed behavior,
reproduction conditions, and potential impact. Remove credentials, personal data, proprietary
source, model prompts, and unrelated logs from the report.

The owner will acknowledge a usable report, assess severity and scope, coordinate remediation,
and publish an advisory when disclosure is safe. No remediation deadline is promised before
triage establishes the issue's impact and operational constraints.

## Supported versions

The project is pre-release. Only the current `main` revision is evaluated for security fixes.
No deployed production service or stable compatibility commitment exists yet.

## Safe research boundary

Do not test against systems, accounts, repositories, providers, credentials, or data you do not
own or have explicit authorization to assess. Do not perform denial-of-service testing, retain
sensitive data, or use a finding to access or alter another party's resources.
