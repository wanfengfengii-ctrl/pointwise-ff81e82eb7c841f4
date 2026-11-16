# 桶布局审查工作区

本仓库是数仓桶文件与关联规划的维护样本，不是通用 SQL 引擎。生产源码为
storage.py、layout.py、table.py、planner.py、join.py、lab.py。
checks.py、evidence_gate.py、samples.json 是须保留的检查与公开材料。
docs/bucket-join.md 是待核准、可能失真的说明，不是正确性依据。
仅交付审查、维护设计和证据，不修改上述生产源码、检查器、样例或原断言。

## 输入、存储和入口

Python 标准库，无安装依赖。write_table(objects, prefix, spec) 生成不可变
part-N.json 桶对象，最后生成 manifest.json；Table(objects, prefix) 可重新打开。
MemoryObjects 与 DirectoryObjects 使用同一 put/get 字节接口。前者用于小型
确定性实验，后者实际写目录；不要求对象写入失败的恢复协议。
Table.read_bucket(i) 读取一个物理桶；Table.read_all() 无规划裁剪地读取全部行。

spec 为对象，完整输入见 samples.json。schema 为 {kind,scale}：
kind 为 i64 时 scale=0，非空 key 是有符号 64 位整数；
kind 为 decimal 时 scale 是 0..6 的固定小数位数，非空 key 是十进制字符串，
必须恰有 scale 位小数，scale=0 时没有小数点；其去掉小数点后的有符号系数
须在 64 位整数范围内。不接收指数、前导加号、布尔值或多余小数位。
key=null 表缺失。rows 为 {id,key,payload} 数组，id 为表内唯一非空字符串，
payload 为字符串；重复数值键完全合法。encoding 为 le-v1 或 text-v1，
buckets 为 2 或 4。范围不含恶意 manifest、可变表、并发写入。

layout.py 定义实际字节编码：类型与 scale 的 ASCII 前缀，加小端 64 位系数
或 ASCII 系数；NULL 使用独立字节。SHA-256 完整摘要视为大端无符号整数，
桶号为该整数模桶数。manifest 公开真实 schema、encoding、hash 与桶数，
family=numeric 描述数值家族，不额外承诺物理字节相等。
Table 的桶行、manifest 与 run_case 的 placements 可相互核对。

execute(left,right,how) 支持 inner/left，返回 pairs 与 trace。
pairs 是 [左行ID,右行ID或null] 的列表，顺序无保证；按多重集核对。
关联按精确数值相等，NULL 不匹配，保留全部重复匹配及 left 未匹配行。
例如整数 1 与定点数 1.00 相等，1.25 不等于 1。负数规则相同。
trace 记录原规划决策、每组实际读取桶及来源行ID，不包含正确性裁决。
lab.run_case(case,objects=None) 从 case 的 left/right spec 建表，按 how 调原入口，
返回实际 pairs、trace 和两表的 manifest/placements；不传 objects 时用内存字节存储。

运行 python3 checks.py 核对正常复用、合桶及基本行语义。
运行 python3 lab.py coalesce 展示真实 4 桶到 2 桶关联。
运行 python3 checks.py incident 可稳定复现原事故：期望 [["fact","dim"]]，
实际 [["fact",null]]，退出码 1，输出 BUCKET_IDENTITY_LOSS。
python3 lab.py incident --directory 一个尚未使用的目录 将同一实验实际落盘。
单表输入可从 samples.json 的 left/right 提取后交给
python3 table.py 输入文件 目标目录；固定对象前缀为 table。
也可直接使用 DirectoryObjects 重新打开已落盘的 left/right 表进行其他关联。

## 本轮维护交付

审查须贯通实际键编码、布局声明、复用判定与执行，定位声明和消费之间的错误承诺。
同一数值采用不同编码时不能据此直接复用桶；相同编码下可证明正确的布局复用和合桶必须保留，不能以关闭全部优化满足维护目标。
不能以汇总相等替代完整来源行ID多重集核对。覆盖两种编码、相同与不同 scale、
2/4 桶、重复键、NULL、空表及 left 未匹配边界；区分单因、必要组合和合法对照，
不要求枚举输入全组合。原事故失败须保留。

交付 REVIEW.md：列出实质根因、源码 path:line、触发调用、独立期望与实际、
对应证据名称和发布意见。维护 docs/bucket-join.md，分清当前行为、承诺缺口和建议。
设计须比较共同数值编码交换与保留一侧原生布局后定向路由，选择并说明兼容性判据、精确转换、不可表示键、NULL及未匹配行的责任归属。
两个组织都须保持可证明的既有复用/合桶；建议按桶执行，不以全表集中逐对关联替代。
允许其他等价组织，契约不预定最终方案。说明须给出足以落实所选组织的写入布局描述、
规划输入/输出、路由与执行接口及关键伪代码，解释旧布局是否可被新判据复用；
不实现生产修复，不要求通用优化器、成本模型、完整理论证明、迁移或恢复系统。
输入持久布局仅有上述两种编码；建议中的临时交换布局可另定精确编码，但不得将它冒充现存布局。
生产中的 gather 兜底仅是现状，不是已提供的共同数值交换或定向路由实现。

## 可重跑证据

新增 review_cases.json，内容为 case 数组，每项含唯一 name、left、right、how，
left/right 沿用上述 spec。新增 review_evidence.py，提供 expected(case) 返回
独立期望 pairs；它只能读取原始输入或完整行，不使用生产编码、分桶、规划或关联判据，
按精确数值逐对关联。可以用标准库精确数值工具。

review_evidence.py --write 实际调用 lab.run_case 重建每例，生成 evidence.json：
顶层 cases 数组按输入顺序，每项为 {name,expected,observed}，observed 是未经删改的
run_case 返回值；另有 locations 数组，每项 {path,line,text}，行号从 1 计，
text 是对应完整源码行。REVIEW.md 须引用这些 case 名称与 path:line。
设计预测不得写入 observed 或冒充已实现结果。
review_evidence.py --demo 展示真实布局、原规划与关联结果及独立期望。

evidence_gate.py 会重新调用原入口和 expected，逐项核对已存证据与源码定位，并打印
实际与期望完整行ID；观察到原缺陷不导致证据门禁失败，伪造或过期证据会失败。
门禁只能检查证据可重放，人工审查仍须核对参照独立性、根因和设计是否成立。
完成后将运行清单的 verify 接到 ["python3","checks.py","delivery"]，
它保留正常回归并调用门禁；demo 接到 ["python3","review_evidence.py","--demo"]。
初始清单仅运行已有正常业务与合桶演示，交付前先 --write，再运行 verify 与 demo。
.pointwise-runtime.json 的 version=1、kind=cli，setup 是准备命令的 argv 列表，
verify/demo 各为单条 argv；本仓库 setup 为空。
