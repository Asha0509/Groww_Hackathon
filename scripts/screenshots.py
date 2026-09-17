"""Regenerates the four screenshots embedded in README.md.

Not part of the test suite and not needed to run or grade the project — it
exists so the images in the README can be rebuilt after a UI change instead
of going quietly stale, which is exactly what happened to the set before it.

    pip install playwright && playwright install chromium   # not in requirements.txt
    uvicorn app.main:app --port 8000                        # in another shell
    python scripts/screenshots.py

Two things that will waste your time otherwise: `app/main.py` reads
`index.html` once at import, so a server started before your edit serves the
old page — restart it (or use `--reload`). And each shot is deliberately
clipped to the region that tells its story rather than captured full-page,
because the page is long enough now that a full-page image is unreadable at
README width.
"""
import asyncio
import sys
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
URL = "http://localhost:8000/"


async def clip(page, top_sel, bottom_sel, pad=14, bottom_pad=2):
    """A box spanning the top of one element to the bottom of another. The
    small bottom pad is on purpose: a larger one reveals the top slice of the
    next table row, which reads as a broken crop.
    """
    top = await page.locator(top_sel).first.bounding_box()
    bottom = await page.locator(bottom_sel).first.bounding_box()
    return {
        "x": max(min(top["x"], bottom["x"]) - pad, 0),
        "y": max(top["y"] - pad, 0),
        "width": max(top["width"], bottom["width"]) + pad * 2,
        "height": (bottom["y"] + bottom["height"]) - top["y"] + pad + bottom_pad,
    }


async def scenario(page, name):
    await page.click(f"#btn-{name}")
    await page.wait_for_function(
        "n => document.getElementById('btn-'+n).classList.contains('active')", arg=name
    )
    await page.wait_for_timeout(350)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        # device_scale_factor=2 so the text stays sharp when GitHub scales the
        # image down. The page itself is capped at max-width 780px, so a wider
        # viewport buys nothing.
        page = await browser.new_page(viewport={"width": 1000, "height": 1100}, device_scale_factor=2)
        await page.goto(URL, wait_until="networkidle")

        # big_move: the MOVE card, before and after "I looked". Both frames use
        # the same box so the pair lines up when read one after the other.
        await scenario(page, "big_move")
        card = page.locator("#digest .card").first
        assert await card.count(), "no digest card rendered — is a watermark already at the latest seq?"
        print("card:", (await card.inner_text()).replace("\n", " | "))
        box = await clip(page, "h1", "#watchlist tr:nth-child(4)")
        await page.screenshot(path=OUT / "before_i_looked.png", clip=box)

        await page.click("#seenBtn")
        await page.wait_for_selector("#digest .empty")
        await page.wait_for_selector("#ackConfirm:not([hidden])")
        print("ack:", await page.locator("#ackConfirm").inner_text())
        await page.screenshot(path=OUT / "after_i_looked.png", clip=box)

        # split_day: naive vs. Since on the one row that has a corporate action.
        await scenario(page, "split_day")
        contrast = page.locator("#watchlist .contrast").first
        assert await contrast.count(), "no naive/since contrast rendered"
        print("contrast:", (await contrast.inner_text()).replace("\n", " "))
        await page.screenshot(
            path=OUT / "corporate_action.png",
            clip=await clip(page, "#watchlist-section h2", "#watchlist tr:nth-child(2)"),
        )

        # feed_death: the DEGRADED card and its row in one frame, since the
        # point is that one instrument is dark while the rest still tick.
        await scenario(page, "feed_death")
        degraded = page.locator("#digest .card-degraded").first
        assert await degraded.count(), "no degraded card rendered"
        print("degraded:", (await degraded.inner_text()).replace("\n", " | "))
        await page.screenshot(
            path=OUT / "degraded_feed.png",
            clip=await clip(page, "#digest-section h2", "#watchlist tr:nth-child(4)"),
        )

        await scenario(page, "normal")
        await browser.close()
    print(f"\nwrote 4 screenshots to {OUT}")


if __name__ == "__main__":
    try:
        httpx.get(URL + "healthz", timeout=3.0).raise_for_status()
    except Exception:
        sys.exit(f"no server at {URL} — start one with: uvicorn app.main:app --port 8000")
    asyncio.run(main())
