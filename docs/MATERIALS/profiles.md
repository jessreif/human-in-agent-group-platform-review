# Profiles

Published manifests in `content/published/manifests.json`: 85.
Profile JSON files: 103.

Reference pair: the model and bank-only versions of the time-pressure study. Hashes are copied from the publication index by this script.

| Profile | manifest_sha256 | bundle_sha256 |
|---|---|---|
| `tp_ai_v7` | `c5b5ef4cf2dd0c194cae9814112d2a54fef09d9d1a0ad80273f25c04bef8198c` | `de6570f1ee459b34db9fc2f9514e4d6ec871f00aa7bf8637327d6d87cbcca01a` |
| `tp_scripted_v9` | `5b844019ae40edc9b33b80ed8c3b7e63501be4b1c1a962707fa2eb11ed218c62` | `9a001b8995682c5c1a7fd0f2a82c32608ecf9a2be3b7193fb555d00b6a07275a` |

## Arm diff from `check_paired_arms`

### `tp_ai_v7`

- `manipulation.arm.arm`: left `'high'` / right `'low'`
- `manipulation.arm.remark_lines`: left `["Yikes this is a lot of info, I don't think I am going to finish on time", 'i know right!']` / right `["Ok this seems manageable, I think I'll finish with time to spare", 'me too! i think']`
- `manipulation.arm.teammate_values`: left `[6, 7]` / right `[2, 3]`

### `tp_scripted_v9`

- `manipulation.arm.arm`: left `'high'` / right `'low'`
- `manipulation.arm.remark_lines`: left `["Yikes this is a lot of info, I don't think I am going to finish on time", 'i know right!']` / right `["Ok this seems manageable, I think I'll finish with time to spare", 'me too! i think']`
- `manipulation.arm.teammate_values`: left `[6, 7]` / right `[2, 3]`

Anything not under `manipulation.arm` would have raised. This output is the allowed difference set.
