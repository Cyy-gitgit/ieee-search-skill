---
name: ieee-search
description: Search and download papers from IEEE Xplore via Chrome CDP. Supports keyword search, year filtering, citation sorting, and PDF download (with Sci-Hub fallback).
argument-hint: "[keyword] | [startYear endYear] | [sortBy] | [count] | [outputDir]"
---

# IEEE Xplore 论文检索下载工具

通过 **Chrome DevTools Protocol (CDP)** + **Playwright** 连接已打开的 Chrome 浏览器，自动化操作 IEEE Xplore（ieeexplore.ieee.org）。支持从搜索→筛选→被引量排序→下载 PDF 的一站式操作，未订阅的论文自动通过 Sci-Hub 回退下载。

## 前置条件

1. **Chrome 浏览器已打开**（已登录机构账号可获取更多下载权限）
2. **Python 3.8+** 且已安装 Playwright：`pip install playwright`
3. Chrome 远程调试端口已开放（`--remote-debugging-port=9222`）

如果 Chrome 未以调试模式启动，自动执行：
```
taskkill /F /IM chrome.exe
start chrome --remote-debugging-port=9222 --no-first-run --user-data-dir="%TEMP%/chrome-debug-profile"
```

## 参数格式

```
关键词 | 起始年 结束年 | 排序方式 | 下载数量 | 输出路径
```

所有参数均**可选**，不提供则使用默认值。

| 参数 | 说明 | 默认值 | 示例 |
|------|------|--------|------|
| 关键词 | 检索关键词 | `refined oil product scheduling optimization` | `deep learning` |
| 年份范围 | 起始年 结束年 | `2020 2026` | `2022 2025` |
| 排序方式 | `citations`(被引) / `date`(日期) | `citations` | `date` |
| 下载数量 | 前 N 篇 | `10` | `5` |
| 输出路径 | 保存文件夹 | `./IEEE_Results` | `D:/papers` |

### 使用示例

```
成品油调度优化
成品油调度优化 | 2020 2026
reinforcement learning | 2020 2025 | citations | 20
machine learning | 2018 2024 | date | 15 | D:/ml_papers
```

## 工作流程

### Step 1: 解析参数

调用 `parse_params()` 从自然语言参数中提取结构化的搜索配置：
- 关键词 (query)
- 年份范围 (start_year, end_year)
- 排序方式 (sort_by: `citations` 或 `date`)
- 下载数量 (count)
- 输出路径 (output_dir)

中文参数自动映射：`被引` → `citations`, `日期`/`发表` → `date`

### Step 2: 连接 Chrome CDP

```python
from playwright.async_api import async_playwright
playwright = await async_playwright().start()
browser = await playwright.chromium.connect_over_cdp("http://localhost:9222")
page = browser.contexts[0].pages[0]
```

### Step 3: 搜索 IEEE Xplore

构建带参数的搜索 URL 并直接导航：

```
https://ieeexplore.ieee.org/search/searchresult.jsp
  ?queryText={关键词}
  &ranges={起始年}_{结束年}_PYear
  &sortType={citations|date}
```

等待页面加载完成后，提取论文信息：
- 通过 `a[href*="/document/"]` 选择器找到所有论文链接
- 提取标题、年份、文档ID
- 根据年份范围过滤（避免 URL 参数未生效的情况）

### Step 4: 批量下载 PDF

**下载优先级：**

1. **IEEE 直接下载** — 通过 `stamp/stamp.jsp?tp=&arnumber={doc_id}` 端点尝试
2. **Sci-Hub 回退** — 先从论文详情页提取 DOI，再用 Sci-Hub 获取 PDF

**Sci-Hub 下载详情：**

尝试多个镜像域名（`sci-hub.ru` → `sci-hub.st` → `sci-hub.sg`），对每个域名：
1. 导航到 `https://{domain}/{doi}`
2. 检查页面是否为 PDF（`contentType`、`embed`、`iframe#pdf`、`object`）
3. 查找 `.pdf` 结尾或含 `/storage/` 的下载链接
4. 验证内容含 `%PDF` 头部后保存

```python
# Sci-Hub PDF 检测逻辑
pdf_info = await page.evaluate("""
    () => {
        let pdfUrl = '';
        // 1. Direct PDF page
        if (document.contentType === 'application/pdf') pdfUrl = window.location.href;
        // 2. Embedded PDF
        const embed = document.querySelector('embed[type="application/pdf"]');
        if (embed?.src) pdfUrl = embed.src;
        // 3. iframe
        const iframe = document.querySelector('iframe#pdf');
        if (iframe?.src) pdfUrl = iframe.src;
        // 4. object
        const obj = document.querySelector('object[type="application/pdf"]');
        if (obj?.data) pdfUrl = obj.data;
        return pdfUrl;
    }
""")
# Fallback: find links ending with .pdf or containing /storage/
```

### Step 5: 保存结果

- PDF 文件：`{序号:02d}_{论文标题}.pdf`
- 论文列表：`papers_list.json`（含标题、链接、年份）

## 已知限制

1. **订阅要求**：IEEE Xplore 的大部分论文需要机构订阅。未订阅的论文会尝试 Sci-Hub，但 Sci-Hub 可能没有最新论文（2025年后的论文收录有限）
2. **Sci-Hub 可用性**：镜像域名可能间歇性不可用，脚本自动轮流尝试多个域名
3. **下载失败常见原因**：
   - 论文太新（2025+），Sci-Hub 尚未收录
   - 网络环境无法访问 Sci-Hub
   - Chrome 调试端口未正确配置
4. **年份过滤**：URL 参数和 JavaScript 提取双重过滤，确保结果准确

## 文件结构

```
.claude/skills/ieee-search/
├── SKILL.md                   # Skill 定义（本文件）
└── scripts/
    └── ieee_search.py         # 核心 Python 脚本
```

## 环境要求

- Python 3.8+
- `playwright` 库：`pip install playwright`
- 无需 `playwright install chromium`（使用已安装的 Chrome 而非 Playwright 自带的 Chromium）
