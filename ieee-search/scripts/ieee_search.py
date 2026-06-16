# -*- coding: utf-8 -*-
"""
IEEE Xplore 论文检索下载工具
通过 Chrome DevTools Protocol (CDP) 连接已打开的 Chrome，自动化操作 IEEE Xplore
支持关键词搜索、年份筛选、被引量排序、PDF 下载（含 Sci-Hub 回退）
"""
import asyncio
import json
import os
import re
import sys
import urllib.parse
from playwright.async_api import async_playwright


def sp(text):
    """Safe print for Windows GBK console"""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode('ascii', 'replace').decode('ascii'))


def parse_params(args_text: str) -> dict:
    """Parse natural language / structured parameters into a config dict.

    Expected format: keyword | startYear endYear | sortBy | count | outputDir
    Example: reinforcement learning | 2020 2025 | citations | 10 | D:/papers
    """
    params = {
        "query": "refined oil product scheduling optimization",
        "start_year": 2020,
        "end_year": 2026,
        "sort_by": "citations",
        "count": 10,
        "output_dir": None,
    }

    if not args_text:
        return params

    parts = [p.strip() for p in args_text.split("|")]

    # Keyword
    if len(parts) >= 1 and parts[0]:
        params["query"] = parts[0]

    # Year range
    if len(parts) >= 2 and parts[1]:
        years = parts[1].split()
        if len(years) >= 1 and years[0].isdigit():
            params["start_year"] = int(years[0])
        if len(years) >= 2 and years[1].isdigit():
            params["end_year"] = int(years[1])

    # Sort by
    if len(parts) >= 3 and parts[2]:
        sort_map = {"被引": "citations", "日期": "date", "发表": "date",
                    "cited": "citations", "citations": "citations",
                    "newest": "date", "date": "date"}
        params["sort_by"] = sort_map.get(parts[2].lower(), "citations")

    # Count
    if len(parts) >= 4 and parts[3] and parts[3].isdigit():
        params["count"] = int(parts[3])

    # Output dir
    if len(parts) >= 5 and parts[4]:
        params["output_dir"] = parts[4]

    return params


async def connect_chrome(port=9222):
    """Connect to an already-running Chrome via CDP."""
    playwright = await async_playwright().start()
    browser = await playwright.chromium.connect_over_cdp(f"http://localhost:{port}")
    ctx = browser.contexts[0]
    pages = ctx.pages
    page = pages[0] if pages else await ctx.new_page()
    for p in pages[1:]:
        await p.close()
    return playwright, browser, page


def build_search_url(query: str, start_year: int, end_year: int, sort_by: str) -> str:
    """Build IEEE Xplore search URL with filters."""
    return (
        "https://ieeexplore.ieee.org/search/searchresult.jsp"
        f"?queryText={urllib.parse.quote(query)}"
        "&highlight=true&returnFacets=ALL&returnType=SEARCH&matchPubs=true"
        f"&ranges={start_year}_{end_year}_PYear"
        f"&sortType={sort_by}"
    )


async def search_ieee(page, query: str, start_year: int, end_year: int, sort_by: str) -> list:
    """Search IEEE and extract paper list."""
    search_url = build_search_url(query, start_year, end_year, sort_by)
    sp(f"  Searching: {query}")
    sp(f"  URL: {search_url}")

    await page.goto(search_url, wait_until="networkidle", timeout=60000)
    await page.wait_for_timeout(5000)

    # Extract paper data
    papers = await page.evaluate("""
        () => {
            const papers = [];
            const seen = new Set();
            document.querySelectorAll('a[href*="/document/"]').forEach(a => {
                const idMatch = a.href.match(/document\\/(\\d+)/);
                if (!idMatch || seen.has(idMatch[1])) return;
                seen.add(idMatch[1]);
                const section = a.closest('div[class], li, article, section') ||
                               a.parentElement?.closest('div, li') || a.parentElement;
                const contextText = section ? section.textContent : '';
                const heading = a.closest('h2, h3, h4');
                const title = heading ? heading.textContent.trim() : a.textContent.trim();
                const yearMatch = contextText.match(/\\b(20[12]\\d)\\b/);
                const year = yearMatch ? yearMatch[1] : '';
                papers.push({ title: title, link: a.href, year: year });
            });
            return papers;
        }
    """)

    # Filter by year
    valid = [p for p in papers if p.get("year", "").isdigit()
             and start_year <= int(p["year"]) <= end_year]
    if len(valid) >= 3:
        papers = valid

    return papers


async def try_direct_pdf(page, doc_id: str) -> bytes:
    """Try direct PDF download via IEEE stamp endpoint."""
    stamp_url = f"https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber={doc_id}"
    resp = await page.context.request.get(stamp_url, timeout=30000)
    if resp:
        content = await resp.body()
        if b"%PDF" in content[:500] or len(content) > 80000:
            return content
    return None


async def try_scihub(page, doi: str) -> bytes:
    """Try Sci-Hub to download paper PDF by DOI."""
    for domain in ["sci-hub.ru", "sci-hub.st", "sci-hub.sg"]:
        try:
            url = f"https://{domain}/{doi}"
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(4000)

            # Check for PDF
            pdf_info = await page.evaluate("""
                () => {
                    let pdfUrl = '';
                    try { if (document.contentType === 'application/pdf') pdfUrl = window.location.href; } catch(e) {}
                    const embed = document.querySelector('embed[type="application/pdf"]');
                    if (embed && embed.src) pdfUrl = embed.src;
                    const iframe = document.querySelector('iframe#pdf');
                    if (iframe && iframe.src) pdfUrl = iframe.src;
                    const obj = document.querySelector('object[type="application/pdf"]');
                    if (obj && obj.data) pdfUrl = obj.data;
                    return pdfUrl.substring(0, 500);
                }
            """)

            if pdf_info:
                try:
                    resp = await page.context.request.get(pdf_info, timeout=30000)
                    if resp:
                        content = await resp.body()
                        if b"%PDF" in content[:500]:
                            return content
                except Exception:
                    pass

            # Fallback: look for storage links
            links = await page.evaluate("""
                () => {
                    const results = [];
                    document.querySelectorAll('a').forEach(a => {
                        const h = a.href || '';
                        if (h && (h.endsWith('.pdf') || h.includes('/storage/'))) {
                            results.push(h);
                        }
                    });
                    return results;
                }
            """)

            for link in links:
                try:
                    resp = await page.context.request.get(link, timeout=30000)
                    if resp:
                        content = await resp.body()
                        if b"%PDF" in content[:500]:
                            return content
                except Exception:
                    pass

        except Exception:
            continue

    return None


async def download_papers(page, papers: list, output_dir: str, count: int = 10) -> int:
    """Download PDFs for papers, returning number successfully downloaded."""
    downloaded = 0

    for i, paper in enumerate(papers[:count]):
        title = paper.get("title", "")
        link = paper.get("link", "")
        doi = paper.get("doi", "")
        year = paper.get("year", "")

        if not link:
            continue

        safe_title = re.sub(r'[<>:"/\\|?*]', "", title)[:80].strip() or f"paper_{i+1}"
        sp(f"  [{i+1}/{min(count, len(papers))}] {safe_title[:55]}...")

        # Extract document ID
        doc_match = re.search(r"/document/(\d+)", link)
        doc_id = doc_match.group(1) if doc_match else None
        if not doc_id:
            sp("    No document ID")
            continue

        # Get DOI from paper page if not already known
        if not doi:
            try:
                await page.goto(link, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2000)
                doi = await page.evaluate("""
                    () => {
                        const a = document.querySelector('a[href*="doi"]');
                        if (a) return a.href;
                        const m = document.body.innerText.match(/10\\.\\d{4,}\\/[\\w.\\/-]+/);
                        return m ? m[0] : '';
                    }
                """)
            except Exception:
                pass

        # Method 1: Direct IEEE PDF
        content = await try_direct_pdf(page, doc_id)
        if content:
            fp = os.path.join(output_dir, f"{i+1:02d}_{safe_title}.pdf")
            with open(fp, "wb") as f:
                f.write(content)
            sp(f"    DOWNLOADED from IEEE ({len(content)} bytes)")
            downloaded += 1
            continue

        # Method 2: Sci-Hub
        if doi:
            sp(f"    Sci-Hub via DOI: {doi[:55]}...")
            content = await try_scihub(page, doi)
            if content:
                fp = os.path.join(output_dir, f"{i+1:02d}_{safe_title}.pdf")
                with open(fp, "wb") as f:
                    f.write(content)
                sp(f"    DOWNLOADED via Sci-Hub ({len(content)} bytes)")
                downloaded += 1
                continue

        sp(f"    Not available (no open access)")

    return downloaded


async def main(args_text: str):
    """Main entry point."""
    params = parse_params(args_text)
    output_dir = params["output_dir"] or os.path.join(os.getcwd(), "IEEE_Results")
    os.makedirs(output_dir, exist_ok=True)

    sp(f"IEEE Xplore Search Tool")
    sp(f"{'='*50}")
    sp(f"Query: {params['query']}")
    sp(f"Year: {params['start_year']}-{params['end_year']}")
    sp(f"Sort: {params['sort_by']}")
    sp(f"Count: {params['count']}")
    sp(f"Output: {output_dir}")
    sp(f"{'='*50}\n")

    # Connect to Chrome
    sp("Step 1: Connecting to Chrome...")
    try:
        playwright, browser, page = await connect_chrome()
    except Exception as e:
        sp(f"  ERROR: Cannot connect to Chrome: {e}")
        sp(f"  Ensure Chrome is running with: --remote-debugging-port=9222")
        sp(f"  Example: start chrome --remote-debugging-port=9222")
        return
    sp("  Connected.\n")

    try:
        # Search
        sp("Step 2: Searching IEEE Xplore...")
        papers = await search_ieee(page, params["query"],
                                    params["start_year"], params["end_year"],
                                    params["sort_by"])
        sp(f"  Found {len(papers)} papers in range {params['start_year']}-{params['end_year']}.")
        for i, p in enumerate(papers[:params["count"]]):
            sp(f"  {i+1:2d}. [{p.get('year','?')}] {p['title'][:70]}")
        sp("")

        if not papers:
            sp("No papers found. Try broader keywords.")
            return

        # Save paper list
        with open(os.path.join(output_dir, "papers_list.json"), "w", encoding="utf-8") as f:
            json.dump(papers[:params["count"]], f, ensure_ascii=False, indent=2)

        # Download
        sp(f"Step 3: Downloading PDFs...")
        dl_count = await download_papers(page, papers, output_dir, params["count"])

        sp(f"\n{'='*50}")
        sp(f"Complete!")
        sp(f"  Papers found: {len(papers)}")
        sp(f"  PDFs downloaded: {dl_count}/{min(params['count'], len(papers))}")
        sp(f"  Files in: {output_dir}")
        sp(f"{'='*50}")

        if dl_count < min(params["count"], len(papers)):
            sp(f"\nNote: Undownloaded papers require IEEE institutional subscription.")
            sp(f"Try with a university network or VPN for full access.")

    finally:
        await browser.close()
        await playwright.stop()


if __name__ == "__main__":
    # Get args from command line or use defaults
    args = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    os.environ["PYTHONIOENCODING"] = "utf-8"
    asyncio.run(main(args))
