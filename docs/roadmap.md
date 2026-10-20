# PolicyNIM roadmap

[#88](https://github.com/nnennandukwe/policyNIM/issues/88) is the accepted program
and implementation order. Read its linked issues and PR evidence for current
status; this page doesn't duplicate release checklists or promise dates.

PolicyNIM's hosted service uses one maintained shared public corpus. Custom policies
remain local or self-hosted files/Git packs, with reviewed composition and pinned
replacements. Remote shell execution and arbitrary operational actions aren't part
of the hosted guidance/evaluation program.

## Workstreams

| Work | Source of requirements |
| --- | --- |
| Service boundaries and MCP foundation | [#89](https://github.com/nnennandukwe/policyNIM/issues/89), [#96](https://github.com/nnennandukwe/policyNIM/issues/96) |
| Source inventory, policy packs and generation publication | [#90](https://github.com/nnennandukwe/policyNIM/issues/90), [#91](https://github.com/nnennandukwe/policyNIM/issues/91), [#92](https://github.com/nnennandukwe/policyNIM/issues/92), [#93](https://github.com/nnennandukwe/policyNIM/issues/93) |
| Hosted identity and accepted-operation limits | [#97](https://github.com/nnennandukwe/policyNIM/issues/97), [#98](https://github.com/nnennandukwe/policyNIM/issues/98) |
| Grounded tools and durable evaluation work | [#99](https://github.com/nnennandukwe/policyNIM/issues/99), [#100](https://github.com/nnennandukwe/policyNIM/issues/100), [#101](https://github.com/nnennandukwe/policyNIM/issues/101) |
| Legacy recovery and measured hosted reliability | [#102](https://github.com/nnennandukwe/policyNIM/issues/102), [#103](https://github.com/nnennandukwe/policyNIM/issues/103) |
| Package, client and rollout proof | [#104](https://github.com/nnennandukwe/policyNIM/issues/104) |

## Evidence before release claims

Code, policy generations, packages, client verification and production deployment
have separate versions and gates. A passing offline suite doesn't establish live
provider behavior or production capacity. Synthetic load receipts must identify
provider doubles, source revision, hardware and configuration.

External OAuth acceptance requires an actually configured issuer and client flow;
existing beta/operator credentials aren't proof of that flow. Keep one hosted
replica until shared-state ownership and scaling have been designed and verified.
The relevant issues own these acceptance checks; metadata changes don't close them.
