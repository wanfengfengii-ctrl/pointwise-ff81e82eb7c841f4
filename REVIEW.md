# 桶布局复用审查报告

审查范围：`storage.py`、`layout.py`、`table.py`、`planner.py`、`join.py`、`lab.py`。
本轮只做审查、文档维护与证据，未修改任何生产源码、检查器、样例或原断言。
`docs/bucket-join.md` 已按审查结论重写。

结论：**不发布。** 规划器把“同一数值家族”误当作“同一物理分桶”，在键签名
不一致时静默复用/合桶，导致相等数值落入不同组而被漏配；现有正常回归只覆盖
同签名，未能暴露该问题。`gather` 兜底不是修复。

## 1. 实质根因

1. **桶身份由键签名决定。** `key_bytes` 把 `kind`、`scale` 写进 ASCII 前缀
   （`layout.py:52`），再按编码追加系数——`le-v1` 8 字节小端
   （`layout.py:54`），`text-v1` ASCII 系数（`layout.py:55`）。摘要覆盖这些
   字节（`layout.py:61`），桶号是摘要模桶数（`layout.py:62`）。即
   `(kind, scale, encoding)` 任一不同，摘要就不同，桶号之间不再有取模关系。
2. **写入按签名分桶。** 行由 `bucket(schema, key, encoding, count)` 落桶
   （`table.py:28`）；manifest 却只承诺 `family='numeric'`
   （`table.py:34`），`encoding`、`buckets` 虽公开（`table.py:35`）但被规划器忽略。
3. **规划判据遗漏签名。** `plan` 只比较 `family`、`hash` 与整除性
   （`planner.py:4-5`），据此外推 `reuse`/`coalesce`
   （`planner.py:8`）。`hash='sha256-v1'` 只说明摘要算法，`family='numeric'`
   只说明数值家族，都不保证键字节相同。
4. **执行器无条件信任对齐。** 按桶号跨步分组（`join.py:14-17`），组内谓词
   本身精确正确（`join.py:28`），left 补 null 正确（`join.py:31-32`）。错误不在
   比较，而在“哪些行被放进同一组比较”。组内无匹配时 left 关联把本应匹配的
   左行输出为 `[id, null]`，形成静默漏配。

一句话：**声明层（`numeric` 家族）与消费层（按签名字节分桶）之间存在错误
承诺**——规划器承诺“可复用”，但持久布局的真实种子是键签名。

## 2. 触发调用与轨迹

原入口：`lab.run_case(case)`（`lab.py:20`）→ `write_table` 两次
（`lab.py:16-17`）→ `execute`（`lab.py:22`）→ `plan`（`join.py:8`）→ 按
`decision['groups']` 跨步读桶（`join.py:14-17`）→ 组内精确比较
（`join.py:28`）→ left 补 null（`join.py:32`）。

事故用例 `incident_original_layout_split` 的实际轨迹（`evidence.json`）：

| 组 | 左桶 | 右桶 | 左行 ID | 右行 ID |
| --- | --- | --- | --- | --- |
| 0 | [0, 2] | [0] | — | `dim` |
| 1 | [1, 3] | [1] | `fact` | — |

`plan.mode=coalesce`，`groups=2`。`fact`（i64/le-v1，摘要桶 3）与
`dim`（decimal(2)/le-v1，摘要桶 0）因签名不同被分到不同组，从未比较，于是
left 关联输出 `[["fact", null]]`。原命令失败保持：
`python3 checks.py incident` 退出码 1，打印 `BUCKET_IDENTITY_LOSS`。

## 3. 分类证据（独立期望 vs 实际）

`review_evidence.expected` 只读原始 `review_cases.json` 的 schema/rows，
用独立的定点解析与 `Fraction` 逐对比较（不使用生产的编码、分桶、规划或
关联判据）。下表的期望/实际均为**完整来源行 ID 多重集**，不是汇总计数。
`MATCH` 的用例是必须保留的合法对照；`MISMATCH` 是当前实现的漏配。

| 用例 | plan | 独立期望（多重集） | 实际（多重集） | 判定 |
| --- | --- | --- | --- | --- |
| `incident_original_layout_split` | coalesce | `{["fact","dim"]}` | `{["fact",NULL]}` | MISMATCH |
| `single_cause_encoding_only` | coalesce | `{["enc_le","enc_text"]}` | `{["enc_le",NULL]}` | MISMATCH |
| `single_cause_scale_only_text` | reuse | `{["scale1","scale2"]}` | `{["scale1",NULL]}` | MISMATCH |
| `single_cause_kind_only` | reuse | `{["kind_i64","kind_dec"]}` | `{["kind_i64",NULL]}` | MISMATCH |
| `necessary_combination_scale_and_encoding` | coalesce | `{["both","both_r"]}` | `{["both",NULL]}` | MISMATCH |
| `necessary_control_scale_only` | coalesce | `{["ctl_scale_l","ctl_scale_r"]}` | `{["ctl_scale_l","ctl_scale_r"]}` | MATCH |
| `necessary_control_encoding_only` | coalesce | `{["ctl_enc_l","ctl_enc_r"]}` | `{["ctl_enc_l","ctl_enc_r"]}` | MATCH |
| `combo_masked_bucket_collision` | reuse | `{["mask_l","mask_r"]}` | `{["mask_l","mask_r"]}` | MATCH |
| `no_false_positive_truncation` | reuse | `{["int_one",NULL]}` | `{["int_one",NULL]}` | MATCH |
| `directed_preserved_right_scale_down` | coalesce | `{["dl","ir"]}` | `{["dl",NULL]}` | MISMATCH |
| `unrepresentable_left_row_still_emitted` | coalesce | `{["frac_left",NULL]}` | `{["frac_left",NULL]}` | MATCH |
| `cross_encoding_decimal_text_le` | coalesce | `{["dec_text","dec_le"]}` | `{["dec_text",NULL]}` | MISMATCH |
| `legit_control_coalesce_duplicates_null` | coalesce | `{["a","x"],["a","y"],["b","x"],["b","y"],["c",NULL],["d",NULL]}` | 同左 | MATCH |
| `legit_control_reverse_coalesce` | coalesce | `{["m","n"],["p",NULL]}` | 同左 | MATCH |
| `legit_control_text_reuse_duplicates` | reuse | `{["t1","u1"],["t1","u2"],["t2","u1"],["t2","u2"],["tn",NULL]}` | 同左 | MATCH |
| `empty_right_left_join` | reuse | `{["L1",NULL],["L2",NULL]}` | 同左 | MATCH |
| `empty_left_left_join` | reuse | `{}` | `{}` | MATCH |
| `both_empty_different_signature` | coalesce | `{}` | `{}` | MATCH |
| `nulls_never_match` | reuse | `{["left_null",NULL]}` | 同左 | MATCH |
| `encoding_duplicates_inner` | coalesce | `{["a1","b1"],["a1","b2"],["a2","b1"],["a2","b2"]}` | `{}` | MISMATCH |

### 3.1 分类说明

- **单因（single cause）**：
  - `single_cause_encoding_only`：仅编码不同（i64，le-v1 vs text-v1），桶
    1 vs 0 与组 1 vs 0 错位，漏配。
  - `single_cause_scale_only_text`：仅 scale 不同（同 text-v1，1 vs 2），
    桶 0 vs 1，漏配。
  - `single_cause_kind_only`：仅 kind 不同（i64 vs decimal(0)，同 le-v1、
    同 scale），桶 1 vs 0，漏配。
  三者各自单独成立，说明 `kind`、`scale`、`encoding` 任一进入签名都足以破坏对齐。
- **必要组合（necessary combination）**：
  `necessary_combination_scale_and_encoding` 中，左为 decimal(1)/le-v1，
  仅改 scale（`necessary_control_scale_only`）对齐，仅改编码
  （`necessary_control_encoding_only`）也对齐，**两者同时改**才错位并漏配。
  两个控制用例是 `MATCH`，证明该组合是必要而非巧合；这也是“不能只修编码
  或只修 scale”的直接证据。
- **组合掩盖（masking）**：`combo_masked_bucket_collision` 的签名同样不同
  （i64/text-v1 vs decimal(2)/text-v1），但 `1` 与 `1.00` 恰好都落在桶 1，
  于是 `MATCH`。这说明**单次桶号相等不能作为正确性证据**；以该用例“通过”
  来宣称可发布，就是被碰撞掩盖。`necessary_combination_*` 的同一风险在
  4 桶下也可能被偶然掩盖，故必须以签名判据而非逐例桶号核对。
- **合法对照（legitimate controls）**：
  - `legit_control_coalesce_duplicates_null`：同签名 4→2，重复键全保留
    （a×x、a×y、b×x、b×y），NULL 不匹配且 `c` 未匹配补 null，`d` 未匹配。
  - `legit_control_reverse_coalesce`：同签名 2→4 角色互换，仍正确。
  - `legit_control_text_reuse_duplicates`：同签名 reuse，重复与 NULL 正确。
  - `empty_right_left_join`/`empty_left_left_join`/`both_empty_different_signature`：
    空表边界；左右空与异签名空表都不应产生伪行。
  - `nulls_never_match`：两侧 NULL 即使都落在桶 1 也不匹配。
  - `unrepresentable_left_row_still_emitted`：左 `1.25` 在右 i64 下不可表示，
    但 left 关联仍须输出 `[frac_left, null]`；当前实现恰好正确，提醒新设计
    不得把“不可表示”误当成“丢弃左行”。
  - `no_false_positive_truncation`：`1` 与 `1.25` 不得因前缀/截断而误配。
- **遗漏与错误方向**：`encoding_duplicates_inner` 显示跨编码重复键会
  丢失全部 4 个笛卡尔匹配（实际空集）；`directed_preserved_right_scale_down`
  显示保留右侧原生布局时左侧 decimal(2) 行仍会漏配；`cross_encoding_decimal_text_le`
  显示同 kind/scale、仅编码不同（text vs le）也漏配。这些用例共同约束设计的
  兼容性判据、方向选择与不可表示键责任。

## 4. 源码定位

以下为 `evidence.json` 的 `locations`，行号从 1 计，门禁逐行核对文本。

| 位置 | 源码行 | 说明 |
| --- | --- | --- |
| `layout.py:43` | `return None if value is None else Fraction(value, 10 ** schema['scale'])` | 精确数值比较域 |
| `layout.py:52` | `prefix = (schema['kind'] + ':' + str(schema['scale']) + ':').encode('ascii')` | kind/scale 进入摘要前缀 |
| `layout.py:53` | `if encoding == 'le-v1':` | 编码选择物理字节 |
| `layout.py:54` | `return prefix + value.to_bytes(8, 'little', signed=True)` | le-v1 小端系数 |
| `layout.py:55` | `return prefix + str(value).encode('ascii')` | text-v1 ASCII 系数 |
| `layout.py:61` | `digest = hashlib.sha256(key_bytes(schema, key, encoding)).digest()` | 摘要覆盖编码字节 |
| `layout.py:62` | `return int.from_bytes(digest, 'big') % count` | 桶号为摘要模桶数 |
| `table.py:28` | `parts[bucket(schema, row['key'], encoding, count)].append(dict(row))` | 写入按签名分桶 |
| `table.py:34` | `'layout': {'family': 'numeric', 'hash': 'sha256-v1',` | 仅声明数值家族 |
| `table.py:35` | `'encoding': encoding, 'buckets': count},` | encoding/桶数已公开 |
| `planner.py:4` | `compatible = (a['family'] == b['family'] and` | 复用只比 family |
| `planner.py:5` | `a['hash'] == b['hash'] and large % small == 0)` | 加整除性但不比签名 |
| `planner.py:8` | `'mode': 'reuse' if small == large else 'coalesce',` | 弱判据决定复用 |
| `join.py:14` | `li = list(range(group, len(left.manifest['files']), groups))` | 左组跨步构造 |
| `join.py:16` | `lrows = [r for i in li for r in left.read_bucket(i)]` | 读取假定对齐的左桶 |
| `join.py:17` | `rrows = [r for i in ri for r in right.read_bucket(i)]` | 读取假定对齐的右桶 |
| `join.py:28` | `if lv is not None and rv is not None and lv == rv:` | 组内精确谓词 |
| `join.py:31` | `if how == 'left' and not matched:` | left 判定未匹配 |
| `join.py:32` | `pairs.append([lrow['id'], None])` | 未匹配左行补 null |
| `lab.py:26` | `'placements': [[r['id'] for r in table.read_bucket(i)]` | 暴露实际放置供核对 |

## 5. 设计建议摘要

完整设计见 `docs/bucket-join.md` 第 4 节。要点：

- **兼容性判据**：`(family, hash, kind, scale, encoding)` 相同才复用/合桶；
  同签名 `reuse` 与 4→2 合桶必须保留（可证明：同字节 → 同摘要 →
  `(d%4)%2==d%2`）。
- **两方案比较**：共同数值编码交换（组织 A，全函数、无不可表示键，但需
  临时物化且不得冒充持久布局）vs 保留一侧原生布局后定向路由（组织 B，建议
  采用：不改持久对象、旧布局可直接被新判据复用、仅新增精确转换/路由）。
- **精确转换**：`v * 10**scale` 必须是整数且系数在 i64 内，否则
  `UNREPRESENTABLE`；不截断、不舍入。
- **责任归属**：转换与不可表示判定在路由层；NULL 与未匹配左行在执行器；
  不可表示的路由侧右行恒不匹配可丢弃，路由侧左行在 left 关联仍须补 null；
  只有未知家族/摘要算法才 `gather`。
- **执行形态**：保留侧按组合桶读取，路由侧单次扫描按目标组归并，组内比较，
  不以全表集中逐对关联替代，也不关闭既有优化。

## 6. 发布意见

**不发布当前版本。** 理由：

1. 现有规划判据无法保证跨签名的桶对齐，已由 6 个 `MISMATCH` 用例
   （含原事故、三个单因、一个必要组合、一个跨编码重复键）稳定复现，且
   `checks.py incident` 保留退出码 1。
2. `combo_masked_bucket_collision` 证明“某些组合恰好通过”不能作为发布依据，
   必须改用签名判据。
3. 合法对照与边界用例（同签名 reuse/coalesce、重复键、NULL、空表、方向互换）
   在现实现下均正确，说明修复应聚焦规划/路由，而非重写精确谓词或关闭优化。

发布前应完成：按 `docs/bucket-join.md` 第 4 节实现签名判据与定向路由（或
共同编码交换），保持同签名复用/合桶；把 `review_cases.json` 全部 `MISMATCH`
转为 `MATCH` 且不改变合法对照的实际输出；保留原事故作为回归。
本轮不实现该修复。

## 7. 可重跑

```
python3 checks.py normal            # 正常回归，保留
python3 checks.py incident          # 原事故，退出码 1，保留
python3 review_evidence.py --write  # 重建 evidence.json（observed 为原样 run_case）
python3 checks.py delivery          # 正常回归 + 证据门禁 verify
python3 review_evidence.py --demo   # 真实布局/原规划/实际/独立期望
```

证据门禁会重放每个用例、校验独立期望、逐行核对上述 20 个源码定位，并打印
实际与期望的完整行 ID。观察到原缺陷不会导致门禁失败；伪造或过期证据会失败。
