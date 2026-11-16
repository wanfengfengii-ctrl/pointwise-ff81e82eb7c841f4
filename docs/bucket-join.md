# 桶布局与关联复用维护说明（审查修订版）

状态：本轮为代码审查与文档维护，生产实现、检查器和原断言均未修改。
下文严格区分**当前行为**（源码事实）、**承诺缺口**（旧说明与事实的偏差）和
**建议设计**（待评审、未实现）。证据见 `REVIEW.md`、`review_cases.json`、
`evidence.json`。

## 1. 键布局的事实

写入器对每个非空键执行下列确定性变换（`layout.py:46-55`、`layout.py:58-62`）：

1. `key_bytes` 先写入 ASCII 前缀 `kind ":" scale ":"`（`layout.py:52`），再按
   `encoding` 追加系数：`le-v1` 为 8 字节小端有符号整数（`layout.py:54`），
   `text-v1` 为 ASCII 十进制系数（`layout.py:55`）。
2. NULL 在加点前缀之前就返回固定字节 `b'NULL'`，因此 NULL 的字节与
   `kind`、`scale`、`encoding` 都无关。
3. 桶号是这些字节的 SHA-256 摘要按大端无符号整数对桶数取模
   （`layout.py:61-62`），桶数只允许 2 或 4（`layout.py:59`）。
4. 写入时逐行调用 `bucket(schema, key, encoding, count)` 决定 part 文件
   （`table.py:28`）；manifest 公开 `schema`、`encoding`、`buckets`、`family`、
   `hash`（`table.py:32-37`）。

由此得到本文的核心事实：**桶身份由 `(kind, scale, encoding)` 这三个键布局
字段共同决定**。三个字段构成一个“键签名”（key signature）。数值相等只说明
比较域相等，不说明签名字节相等，也不说明摘要相同。

可证明的推论（同签名/合桶）：

- 若两侧签名完全相同，则任意相等的数值键产生完全相同的字节、相同的摘要，
  于是 `digest % 4` 与 `digest % 2` 天然满足
  `(digest % 4) % 2 == digest % 2`。因此同桶数复用与 4→2（或 2→4 的角色互换）
  合桶是**可证明正确**的，必须保留。
- 若两侧签名不同，摘要输入不同，桶号之间**没有**取模关系。某一次数据恰好
  落在同一桶号（见 `combo_masked_bucket_collision`）只是碰撞，不是规则。

## 2. 当前行为

- 规划器只比较 `family`、`hash` 和桶数整除性（`planner.py:4-5`），据此选择
  `reuse` 或 `coalesce`（`planner.py:8`）。`hash='sha256-v1'` 只表示摘要算法，
  不表示键字节的种子；`family='numeric'` 只是数值家族标签。
- 执行器不再复核签名，直接按桶号跨步分组（`join.py:14-17`），组内用精确
  数值谓词比较（`join.py:28`），left 关联补 null（`join.py:31-32`）。
- 结果：任何签名差异（`kind`、`scale`、`encoding` 任一不同）都可能把相等
  数值放进不同组，造成静默漏配；谓词本身是正确的，错在“哪些行被放在一起
  比较”。原事故 `incident_original_layout_split` 即 i64/le-v1/4 与
  decimal(2)/le-v1/2 的签名差异。

## 3. 承诺缺口（旧说明的错误承诺）

旧版 `docs/bucket-join.md` 的下列句子与源码不符，已在此纠正：

| 旧承诺 | 事实 |
| --- | --- |
| `numeric` 表示同一可比较数值域，可据此直接复用 | `numeric` 只是家族标签；复用需要键签名相同 |
| 整数与定点数的物理编码差异不影响完整性 | 编码进入摘要，编码差异会改变桶号，能导致漏配 |
| `le-v1`/`text-v1` 仅影响存储表达，规划器无须纳入 | 编码是摘要输入的一部分，必须纳入兼容性判据 |
| 4 桶到 2 桶按桶号取模合并即可 | 只有同签名时成立；异签名下桶号之间无取模关系 |
| 正常回归已覆盖，任意组合可省略交换、可发布 | 正常回归只覆盖同签名；异签名漏配未被覆盖 |

旧说明不能再作为正确性依据；`REVIEW.md` 的证据表给出了 20 个可重跑用例，
其中 6 个在当前实现下漏配、1 个因桶碰撞而被掩盖、其余为合法对照。

## 4. 建议设计（未实现）

### 4.1 兼容性判据

以键签名作为复用判据，而不是以 `family` 替代：

```
signature(layout, schema) = (layout.family, layout.hash,
                             schema.kind, schema.scale, layout.encoding)
```

- 签名相同：沿用现有 `reuse`（桶数相等）或 `coalesce`（桶数整除，按
  `i % groups` 合并）。这是必须保留的既有优化。
- 签名不同、家族与摘要算法一致：进入**定向路由**（4.2）。不得回退为全表
  集中逐对关联；`gather` 只保留给未知家族/未知摘要等无法判定的输入。
- 旧 manifest 已包含 `schema.kind`、`schema.scale`、`layout.encoding`，因此
  新判据可直接从现有持久对象推导签名，**旧布局无须迁移即可被新判据复用**：
  同签名旧布局继续复用/合桶；异签名旧布局改走定向路由。若未来写入器新增
  显式 `key_signature` 字段，读取端应对缺失字段回退到上述推导。

### 4.2 两种组织与选择

**组织 A：共同数值编码交换。** 两侧都转换到一种规范数值编码（例如
`numv1`：归一化的符号 + 十进制有效数字 + 尺度，或等价的约分有理数），得到
统一签名后复用/合桶。对契约内输入（i64 系数、scale 0..6）规范编码是**全函数**，
不存在不可表示键；NULL 单独标记。代价是两侧都要写临时交换布局，且该布局
不得冒充现存持久布局（应使用独立 family 或命名空间）。

**组织 B：保留一侧原生布局后定向路由（建议采用）。** 选择一侧 P 作为保留侧，
其不可变 part 文件与桶号原样使用；另一侧 Q 的每一行按精确数值转换到 P 的
签名，计算其在 P 中的目标组，只与目标组比较。不重写任何持久对象，同签名
复用/合桶原样保留，异签名也能按桶执行。

选择 B 的理由：不引入临时物化与“临时布局是否可冒充持久布局”的治理风险；
直接复用旧布局；实现面只增加一个精确转换/路由函数。A 保留为可选方案，适合
同一对表被反复关联、需要摊薄交换成本的场景；两者都不关闭既有优化。

两种组织共有的执行形态：保留侧按组读取（组数 `g` 必须整除保留侧桶数，
2/4 下取 `g = min(nP, nQ)` 或 `gcd`），路由侧单次扫描后按目标组归并；
比较仍限定在同一组内，避免全表笛卡尔式关联。

### 4.3 精确转换与不可表示键

```
encode_exact(sig, v):        # v 为精确有理数，非 NULL
    scaled = v * 10**sig.scale
    if scaled.denominator != 1: return UNREPRESENTABLE
    c = int(scaled)
    if not MIN_I64 <= c <= MAX_I64: return UNREPRESENTABLE
    return prefix(sig) + (c.to_bytes(8, 'little', signed=True)
                          if sig.encoding == 'le-v1' else str(c).encode())
```

不截断、不舍入、不使用浮点。关键引理：**若 `v` 在保留侧签名下不可表示，
则保留侧不存在数值等于 `v` 的行**（保留侧行都由同一签名写入，必然可表示）。
因此把 `UNREPRESENTABLE` 当作“不可能匹配”是可靠的，而不是丢弃仍可能匹配
的行。例：保留侧 i64，路由侧 `1.25` 不可表示且确实不可能等于任何整数；
保留侧 decimal(1)，路由侧 `1.25` 不可表示且确实不可能等于任何一位小数。

### 4.4 责任归属

| 事项 | 责任组件 | 规则 |
| --- | --- | --- |
| 键签名写入与声明 | 写入器 `layout`/`table` | 继续写 `kind/scale/encoding`；签名可由现有字段推导，不新增强制字段 |
| 复用/合桶判定 | 规划器 | 仅签名相同才 `reuse`/`coalesce`；异签名给 `directed` |
| 精确转换 | 路由层（新增，复用 `layout` 的字节规则） | `encode_exact` 唯一转换点，禁止舍入 |
| 不可表示键 | 路由层 + 执行器 | 路由层返回 `UNREPRESENTABLE`；执行器按方向处理：路由侧是右则丢弃（恒不匹配）；路由侧是左时 inner 丢弃、left 输出 `[id, null]` |
| NULL | 执行器 | 不参与路由、不参与比较；保留侧 NULL 随其所在桶读取，left 时输出 null；路由侧 NULL 直接按“不匹配”处理 |
| 未匹配左行 | 执行器 | 保留侧为左：左行始终在原生桶内，逐行判定后补 null；保留侧为右：左行经路由落入唯一组，组内未匹配即补 null；不可表示左行在路由阶段直接补 null |
| 失败/未知布局 | 规划器 | 仅在家族或摘要算法未知时 `gather`，并显式标注原因，不用它掩盖已知签名 |

### 4.5 规划输入/输出与执行接口（关键伪代码）

```
plan(left, right):
    a, b = signature(left), signature(right)
    if a.family != b.family or a.hash != b.hash:
        return {mode:'gather', groups:1, reason:'unknown family/hash'}
    if a.signature == b.signature:
        g = min(a.buckets, b.buckets)              # g 整除两侧桶数
        return {mode:'reuse' if a.buckets==b.buckets else 'coalesce',
                groups:g, alignment:'identical-signature'}
    preserved = 'left'                              # 让未匹配左行留在原生桶
    p, q = (a,b) if preserved=='left' else (b,a)
    g = min(a.buckets, b.buckets)                   # g 整除 p.buckets
    return {mode:'directed', preserved:preserved, groups:g,
            target_signature:p.signature, routed_side:q.name,
            routing:'exact-value', reason:'distinct signatures'}

execute_directed(L, R, how, dec):
    P, Q = (L,R) if dec.preserved=='left' else (R,L)
    groups = [[] for _ in range(dec.groups)]
    direct_null = []                                # 路由侧为左且不可表示
    for row in Q.read_all():                        # 单次扫描，流式
        v = numeric_value(Q.schema, row.key)
        if v is None: continue                      # NULL 恒不匹配
        t = route(dec.target_signature, dec.groups, v)
        if t is UNREPRESENTABLE:
            if how=='left' and Q is L: direct_null.append([row.id, None])
            continue
        groups[t].append(row)
    pairs = []
    for g in range(dec.groups):
        p_rows = [r for i in P.bucket_indices() if i % dec.groups == g
                    for r in P.read_bucket(i)]      # 保留侧按组合桶
        q_rows = groups[g]
        for l in (p_rows if P is L else q_rows):
            matched = False
            for r in (q_rows if P is L else p_rows):
                lv, rv = numeric_value(...), numeric_value(...)
                if lv is not None and rv is not None and lv == rv:
                    pairs.append([l.id, r.id]); matched = True
            if how=='left' and not matched: pairs.append([l.id, None])
    return pairs + direct_null
```

接口约定：`signature(table) -> (family, hash, kind, scale, encoding)`；
`route(target_signature, groups, exact_value) -> group | NULL | UNREPRESENTABLE`；
`plan -> decision`；`execute` 依据 `decision.mode` 分派。`join.py:28` 的精确
数值谓词保持语义不变，路由只决定“哪些行进入同一组”。

## 5. 验证边界与非目标

- 契约范围：整数、固定小数位定点数、NULL 的单键 inner/left 关联；精确数值
  相等；保留重复匹配与左侧未匹配行。
- 必须保留的既有优化：同签名 `reuse`、同签名 4→2/2→4 `coalesce`；回归见
  `checks.py normal` 与 `review_cases.json` 的 `legit_control_*`。
- 本轮不实现生产修复；不提供通用优化器、成本模型、迁移/恢复系统，也不把
  `gather` 兜底或临时交换布局冒充为已实现的共同编码/定向路由。
- 证据可重跑：`python3 review_evidence.py --write` 重建 `evidence.json`；
  `python3 checks.py delivery` 保留正常回归并调用证据门禁；
  `python3 review_evidence.py --demo` 展示真实布局、原规划、实际结果与独立期望。
