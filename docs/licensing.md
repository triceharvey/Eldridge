# Licensing and Third-Party Boundary

Eldridge's original source code and documentation are licensed under the Apache License 2.0.
The repository's `LICENSE` file is authoritative. This document explains the operational
boundary; it does not replace the license or provide legal advice.

## What Apache-2.0 permits

Subject to the license conditions, a person or organization may use, copy, modify,
distribute, sublicense, and commercially deploy Eldridge. The license includes an explicit
patent grant from each contributor for patent claims necessarily infringed by that
contributor's contribution. It provides the software without warranties and does not grant
rights to project names, trademarks, or branding.

A redistribution must include the license, retain applicable notices, identify modified
files, and preserve attribution notices required by the license. If Eldridge later adds a
`NOTICE` file, redistributors must preserve the applicable contents as Section 4 describes.
No `NOTICE` file is currently required by this repository.

## What the project license covers

Apache-2.0 covers Eldridge material that the repository owner and accepted contributors have
the right to license, including the control-plane implementation, provider interfaces,
policy engine, tests, deployment examples, and project documentation.

The license does not automatically cover or relicense:

- OpenAI, Anthropic, Devin, Windsurf, Ollama, or other provider services, models, SDKs, or
  trademarks;
- third-party Python packages, container images, GitHub Actions, or other dependencies;
- prompts, source code, datasets, credentials, or artifacts supplied from an operator's
  separate project;
- model output merely because Eldridge routed, reviewed, or stored it; or
- repositories and infrastructure managed through an Eldridge deployment.

Those materials remain governed by their own licenses, service terms, ownership rules, and
acceptable-use requirements. A provider adapter establishes technical interoperability; it
does not imply endorsement, affiliation, or a transfer of provider intellectual property.

## Contributions and AI-assisted changes

Unless a contribution explicitly states otherwise and is accepted on that basis,
contributions are submitted under Apache-2.0. A contributor must have the right to submit the
material. AI assistance does not remove that responsibility: contributors must review
generated changes, avoid copying incompatible material, identify third-party code, and comply
with the terms of every model, service, dataset, and dependency used to produce the change.

Pull requests that add or copy third-party material must identify its source, version,
license, and any attribution or redistribution requirements. Incompatible or unclear material
must not be merged until the owner records a defensible disposition.

## Why Apache-2.0 instead of MIT

Both licenses are permissive and allow commercial and closed-source use. MIT is shorter and
primarily requires preservation of its copyright and permission notice. Apache-2.0 adds a
more explicit contribution model, a defined patent license and patent-termination mechanism,
modified-file notice requirements, and a structured path for attribution notices.

That additional clarity fits a multi-provider engineering control plane intended to accept
contributions, integrate with commercial systems, and become a serious public portfolio
project. It does not make provider contracts, dependency review, or output provenance
optional; those remain separate governance controls.
