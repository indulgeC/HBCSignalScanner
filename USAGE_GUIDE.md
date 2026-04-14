# Signal Scanner — 完整使用指南

## 一、环境准备

### 1.1 系统要求

- Python 3.10+
- macOS / Linux / Windows
- 网络访问（需要能访问目标政府网站）
- （可选）Anthropic API Key — 用于 AI 生成 Signal Title 和 Strategic Notes

### 1.2 安装

```bash
# 解压项目
tar xzf signal-scanner.tar.gz
cd signal-scanner

# 安装依赖
pip install -r requirements.txt
```

依赖清单：

| 包 | 用途 |
|---|---|
| requests + beautifulsoup4 | 网页抓取与解析 |
| PyYAML | 读取配置文件 |
| PyMuPDF (fitz) | PDF 文本提取 |
| openpyxl + pandas | Excel 生成 |
| anthropic | AI 字段生成（可选） |
| click | 命令行界面 |

### 1.3 设置 API Key（可选）

```bash
# macOS / Linux
export ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxx

# Windows PowerShell
$env:ANTHROPIC_API_KEY = "sk-ant-api03-xxxxxxxx"

# 或在 .env 文件中写入
echo "ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxx" > .env
```

不设置 API Key 时，程序会自动退回到纯规则模式——所有字段仍然可以生成，只是 Signal Title 和 Strategic Notes 会用模板而非 AI 生成。


## 二、项目目录结构

```
signal-scanner/
├── config/                     # ← 配置层
│   ├── sectors.yaml            #    行业关键词包
│   └── sites/
│       └── miami_beach.yaml    #    站点配置（每个站点一个文件）
├── crawler/
│   └── discover.py             # ← 发现层：广度优先爬虫
├── parsers/
│   ├── html_parser.py          # ← 解析层：HTML 页面拆分
│   └── pdf_parser.py           #    PDF 文本提取与拆分
├── classifiers/
│   ├── relevance.py            # ← 主题筛选层
│   ├── rules.py                # ← 规则分类层（17+ 个字段）
│   ├── llm_enrichment.py       # ← AI 补充层
│   ├── project_matcher.py      # ← Phase 3：跨文件项目匹配
│   └── project_tracker.py      #    Phase 3：时间序列 + Momentum
├── models/
│   └── signal.py               # ← 数据模型
├── exporters/
│   └── excel.py                # ← 导出层：Excel + CSV
├── pipeline.py                 # ← 主流水线
├── cli.py                      # ← 命令行入口
├── app.py                      # ← Streamlit Web 界面
├── test_integration.py         # ← 集成测试（含样本数据）
└── requirements.txt
```


## 三、命令行使用（CLI）

### 3.1 基本命令格式

```bash
python cli.py --site <站点名> --sector <行业> [选项]
```

### 3.2 所有参数

| 参数 | 缩写 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `--site` | `-s` | ✅ | — | 站点配置名，对应 `config/sites/` 下的 YAML 文件名（不含 `.yaml`）。可重复 |
| `--sector` | `-S` | ✅ | — | 要筛选的行业。可重复 |
| `--output` | `-o` | — | `data/output/signals.xlsx` | 输出 Excel 文件路径 |
| `--config-dir` | `-c` | — | `config` | 配置文件目录 |
| `--max-pages` | — | — | 站点配置中的值 | 覆盖每个站点的最大抓取页面数 |
| `--threshold` | — | — | `0.05` | 相关性分数下限（0.0–1.0） |
| `--no-llm` | — | — | `false` | 禁用 AI 生成，纯规则模式 |
| `--no-merge` | — | — | `false` | 不合并同一项目的多条信号（仍计算 Momentum） |
| `--verbose` | `-v` | — | `false` | 显示详细日志 |

### 3.3 使用示例

```bash
# ─── 最简单：Miami Beach + stormwater ───────────────────
python cli.py -s miami_beach -S stormwater

# ─── 三个行业一起扫 ─────────────────────────────────────
python cli.py -s miami_beach -S stormwater -S water -S sewer

# ─── 限制抓取量（调试用）──────────────────────────────────
python cli.py -s miami_beach -S stormwater --max-pages 30

# ─── 不用 AI，纯规则模式 ──────────────────────────────────
python cli.py -s miami_beach -S stormwater --no-llm

# ─── 不合并项目（每条信号单独一行）─────────────────────────
python cli.py -s miami_beach -S stormwater --no-merge

# ─── 自定义输出路径 ───────────────────────────────────────
python cli.py -s miami_beach -S stormwater -o results/jan_scan.xlsx

# ─── 提高相关性门槛（只保留强相关信号）────────────────────
python cli.py -s miami_beach -S stormwater --threshold 0.15

# ─── 详细日志输出 ─────────────────────────────────────────
python cli.py -s miami_beach -S stormwater -v

# ─── 多个站点 + 多个行业 + 全部选项 ───────────────────────
python cli.py \
  -s miami_beach \
  -s another_site \
  -S stormwater \
  -S water \
  -S sewer \
  --max-pages 100 \
  --threshold 0.08 \
  --no-llm \
  --no-merge \
  -o results/full_scan.xlsx \
  -v
```

### 3.4 输出文件

每次运行产出两个文件：

```
data/output/signals.xlsx     ← 格式化 Excel（带颜色、超链接、筛选器）
data/output/signals.csv      ← 纯文本 CSV（同名路径）
```


## 四、Streamlit Web 界面

### 4.1 启动

```bash
streamlit run app.py
```

浏览器会自动打开 `http://localhost:8501`。

### 4.2 界面操作

1. **左侧边栏**：选择 Sites、Sectors、高级选项
2. **点击 "Run Scan"**：启动扫描
3. **主区域**：看到结果表、筛选器、统计卡片
4. **底部**：下载 Excel / CSV


## 五、配置详解

### 5.1 添加新站点

在 `config/sites/` 下新建一个 YAML 文件，例如 `config/sites/fort_lauderdale.yaml`：

```yaml
name: fort_lauderdale
display_name: "City of Fort Lauderdale"
base_url: "https://www.fortlauderdale.gov"
default_agency: "City of Fort Lauderdale"
default_geography: "Fort Lauderdale, FL"

# 允许爬虫访问的域名
allowed_domains:
  - "fortlauderdale.gov"
  - "www.fortlauderdale.gov"

# 入口页面（爬虫从这里出发）
seeds:
  - url: "https://www.fortlauderdale.gov/meetings"
    category: meetings         # meetings / procurement / cip / budget
    label: "City Meetings"
  - url: "https://www.fortlauderdale.gov/procurement"
    category: procurement
    label: "Procurement"

# 优先抓取：URL 中包含这些字符串的页面会被优先处理
priority_patterns:
  - "/meetings/"
  - "/procurement/"
  - "/cip/"
  - "/agenda"

# 忽略：URL 中包含这些字符串的页面会被跳过
ignore_patterns:
  - "/careers/"
  - "/parks/"
  - ".jpg"
  - ".png"
  - ".css"
  - ".js"

# 爬虫参数
max_depth: 3                   # 从 seed 出发最多走几层链接
max_pages: 200                 # 最多抓多少页
request_delay_seconds: 1.5     # 每次请求间隔（秒）
```

然后直接用：

```bash
python cli.py -s fort_lauderdale -S stormwater
```

### 5.2 站点配置字段说明

| 字段 | 说明 |
|---|---|
| `name` | 站点标识符，和文件名一致 |
| `display_name` | 显示名称 |
| `base_url` | 站点主 URL |
| `default_agency` | 当文档中没有检测到机构名时的默认值 |
| `default_geography` | 默认地理范围 |
| `allowed_domains` | 爬虫只会访问这些域名下的页面 |
| `seeds` | 入口 URL 列表，每个含 `url`、`category`、`label` |
| `seeds[].category` | 四选一：`meetings` / `procurement` / `cip` / `budget` |
| `priority_patterns` | URL 中含这些字符串的页面会被优先抓取 |
| `ignore_patterns` | URL 中含这些字符串的页面会被跳过 |
| `max_depth` | 链接跟踪深度上限 |
| `max_pages` | 页面抓取总数上限 |
| `request_delay_seconds` | 请求间隔，避免被封 |

### 5.3 添加新行业

编辑 `config/sectors.yaml`：

```yaml
sectors:
  stormwater:
    keywords:
      - stormwater
      - storm water
      - drainage
      - flood control
      # ...更多关键词

  water:
    keywords:
      - drinking water
      - potable water
      - water main
      # ...

  sewer:
    keywords:
      - sewer
      - wastewater
      - force main
      # ...

  # ← 新增行业
  transportation:
    keywords:
      - roadway
      - bridge
      - sidewalk
      - pavement
      - traffic signal
      - intersection
      - transit
```

然后：

```bash
python cli.py -s miami_beach -S transportation
```

关键词设计原则：
- 同时包含全称和缩写（`stormwater` + `storm water`）
- 包含相关设施名（`pump station`、`outfall`、`culvert`）
- 包含相关行动词（`resiliency`、`flood mitigation`）
- 不要太泛（避免单独用 `water` 作为 stormwater 的关键词，会误伤）


## 六、输出字段说明

### 6.1 显示字段（17 列）

| # | 字段 | 生成方式 | 说明 |
|---|---|---|---|
| 1 | Signal Title | AI / 规则 | [机构] + [动作] + [项目对象] |
| 2 | Agency | 规则 | 从文档提取，回退到站点默认值 |
| 3 | Geography | 规则 | 地理范围，如 "Miami Beach, FL (North Beach)" |
| 4 | Sector | 你的输入 | 你选的行业标签 |
| 5 | Estimated Value | 规则 + AI | 文档中的金额，AI 帮挑最可能的那个 |
| 6 | Expected Timeline | 规则 | 根据阶段默认值 + 文本中的明确时间 |
| 7 | Meeting Date | 规则 | 会议/文档日期 |
| 8 | Signal Type | 规则 | Commission Agenda / Capital Budget / Procurement / Policy / Funding |
| 9 | Procurement Stage | 规则 | 从最具体到最模糊：Active Contract → ... → Concept |
| 10 | Lifecycle Stage | 派生 | 从 Procurement Stage + Signal Type 自动推导 |
| 11 | Signal Strength | 规则 + AI | High / Medium / Low（评分制） |
| 12 | Strategic Fit | 规则 + AI | Strong Fit / Moderate Fit / Monitor / No Fit |
| 13 | Friction Level | 规则 | Low / Moderate / High（阻力词检测） |
| 14 | Momentum | Phase 3 | Accelerating / Stable / Stalled / Unclear |
| 15 | Trigger Event | 规则 | 触发事件短语 |
| 16 | Strategic Notes | AI / 规则 | 两句话：触发原因 + 下一步 |
| 17 | Source Link | 原始 | 来源页面 URL |

### 6.2 审计字段（6 列，Excel 中在显示列右侧）

| 字段 | 说明 |
|---|---|
| Evidence Snippet | 匹配到的原文片段 |
| Evidence Page | PDF 页码 |
| Confidence | 相关性分数 (0–1) |
| Method | `rule` / `ai` / `merged` / `derived` |
| File URL | 下载的文件本地路径 |
| Page URL | 原始网页 URL |

### 6.3 Excel 颜色编码

| 字段 | 绿色 | 黄色 | 蓝色 | 红色 | 灰色 |
|---|---|---|---|---|---|
| Signal Strength | High | Medium | — | Low | — |
| Strategic Fit | Strong Fit | Moderate Fit | Monitor | No Fit | — |
| Momentum | Accelerating | — | Stable | Stalled | Unclear |
| Friction Level | Low | Moderate | — | High | — |


## 七、处理流程详解

程序执行的完整流程：

```
1. 读取配置
   config/sites/miami_beach.yaml → 站点信息
   config/sectors.yaml           → 关键词包

2. 爬虫发现（crawler/discover.py）
   访问 seed URLs → 提取子链接 → 优先抓取高价值栏目
   下载 HTML 页面和 PDF 文件

3. 内容解析（parsers/）
   HTML → 按 agenda item / solicitation / project 拆分
   PDF  → 按 agenda item / budget line / page 拆分
   每个文件可能产出多条 chunk

4. 相关性筛选（classifiers/relevance.py）
   每个 chunk × 每个 sector → 打分
   低于 threshold 的丢弃

5. 规则分类（classifiers/rules.py）
   Procurement Stage, Signal Type, Lifecycle, Timeline,
   Estimated Value, Friction, Strength, Fit, Trigger Event,
   Agency, Geography, Meeting Date

6. AI 补充（classifiers/llm_enrichment.py）——可选
   Signal Title, Strategic Notes, Value 选择, Strength 微调, Fit 微调

7. Phase 3 项目合并（classifiers/project_matcher.py + project_tracker.py）
   ID 匹配 + 模糊匹配 → 分组
   同一项目合并成一行
   计算 Momentum 和 Friction

8. 导出（exporters/excel.py）
   格式化 Excel + CSV
```


## 八、常见使用场景

### 场景 1：快速扫一下有没有 stormwater 的机会

```bash
python cli.py -s miami_beach -S stormwater --max-pages 30 --no-llm
```

30 秒内出结果，纯规则模式，不调 API。

### 场景 2：做一次完整扫描交报告

```bash
python cli.py \
  -s miami_beach \
  -S stormwater -S water -S sewer \
  -o reports/miami_beach_$(date +%Y%m%d).xlsx
```

三个行业全扫，AI 生成 Title 和 Notes，带日期输出。

### 场景 3：只看原始信号不合并

```bash
python cli.py -s miami_beach -S stormwater --no-merge
```

每条信号独立一行，可以看到同一项目在不同来源的每个提及。

### 场景 4：对比两个站点

```bash
python cli.py -s miami_beach -s fort_lauderdale -S stormwater
```

两个站点一起扫，输出到同一张表。

### 场景 5：提高精确度（只要强相关信号）

```bash
python cli.py -s miami_beach -S stormwater --threshold 0.20
```

threshold 从默认 0.05 提高到 0.20，只保留高度相关的信号。


## 九、自测

项目自带集成测试，用样本 HTML 模拟 Miami Beach 的 Commission Agenda、Procurement 和 CIP 页面，不需要网络：

```bash
python test_integration.py
```

预期输出：12 条原始信号 → 11 条合并信号（Water Main Replacement 跨 Agenda + CIP 合并），包含三个行业的信号。


## 十、常见问题

### Q: 没有 API Key 能用吗？
可以。加 `--no-llm` 或不设置 `ANTHROPIC_API_KEY`，程序自动退回规则模式。Signal Title 会用 "Agency — 首行文字" 格式，Strategic Notes 会用模板生成。

### Q: 抓取被网站封了怎么办？
调大 `request_delay_seconds`（站点配置中），或减少 `--max-pages`。默认延迟 1.5 秒。

### Q: 有些页面是 JavaScript 动态加载的
当前版本用 requests + BeautifulSoup，不执行 JS。如果遇到动态页面（如 Novus Agenda），需要后续集成 Playwright。这在架构中已预留接口。

### Q: Estimated Value 显示为空
这是正确行为。根据设计原则：
- 文档中有明确金额 → 提取
- 有多个金额 → AI 挑最可能的
- 没有明确金额 → **留空**，不让 AI 编造数字

### Q: Momentum 全显示 Unclear
Momentum 需要同一项目在**多个来源**或**多次扫描**中出现才能计算。单一来源 = Unclear。随着你持续扫描并积累数据，这个字段会越来越有价值。

### Q: 怎么增加对 .docx 文件的支持？
在 `parsers/` 下新建 `docx_parser.py`，用 `python-docx` 库提取段落文本，返回 `List[ParsedChunk]`。然后在 `pipeline.py` 的 `_parse_result` 方法中加一个 `elif cr.local_path.endswith(".docx")` 分支。
