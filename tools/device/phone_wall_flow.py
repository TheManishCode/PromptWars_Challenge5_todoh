import sys,time,subprocess
sys.path.insert(0,".")
from tests.test_wall_frontend_e2e import OBSERVE_THREE, BOARD_POINT
from playwright.sync_api import sync_playwright
import os
DB=os.environ["EVENTMATE_WALL_DB"]
with sync_playwright() as p:
    br=p.chromium.connect_over_cdp("http://127.0.0.1:9333")
    page=[pg for c in br.contexts for pg in c.pages if pg.url.startswith("http://localhost:8791")][0]
    cdp=page.context.new_cdp_session(page)
    def touch(kind,x=None,y=None): cdp.send("Input.dispatchTouchEvent",{"type":kind,"touchPoints":[] if x is None else [{"x":x,"y":y}]})
    def tap(x,y): touch("touchStart",x,y); touch("touchEnd")
    def swipe(x,y,dx,steps=14):
        touch("touchStart",x,y)
        for i in range(1,steps+1): touch("touchMove",x+dx*i/steps,y); time.sleep(0.03)
        touch("touchEnd")
    page.add_init_script(OBSERVE_THREE); page.reload(); page.wait_for_timeout(9000)
    page.evaluate("sessionStorage.clear()")
    def focus():
        for _ in range(4):
            pt=page.evaluate(BOARD_POINT,None); tap(pt["x"],pt["y"]); page.wait_for_timeout(1500)
            if page.evaluate("document.body.classList.contains('wall-focus')"): return True
        return False
    print("focus:",focus()); page.wait_for_timeout(1200)
    pt=page.evaluate(BOARD_POINT,None)
    for dx in (140,-40,60):   # drag sideways with a finger until the scratches sit true
        swipe(pt["x"]-60,pt["y"],dx)
        if page.evaluate("sessionStorage.getItem('em_wall_solved')")=="1": break
    print("puzzle solved by finger drag:",page.evaluate("sessionStorage.getItem('em_wall_solved')")=="1")
    page.wait_for_timeout(1000)
    print("sign box visible:",page.evaluate("!document.querySelector('#wall-signbox').hidden"))
    page.screenshot(path="/tmp/phone_sign.png")
    page.fill("#wall-name","Phone Test"); page.click("#wall-sign-btn"); page.wait_for_timeout(2000)
    print("after sign:",page.evaluate("document.querySelector('#wall-mine').textContent"))
    out=subprocess.run([sys.executable,"-m","eventmate.wall_admin","--db",DB,"approve","1"],capture_output=True,text=True); print("approve:",out.stdout.strip() or out.stderr.strip()[:100])
    page.keyboard.press("Escape"); page.wait_for_timeout(1500)
    print("refocus:",focus()); page.wait_for_timeout(2000)
    print("challenge visible:",page.evaluate("!document.querySelector('#wall-challenge').hidden"))
    page.click("#wall-start-timed"); page.wait_for_timeout(1200)
    rnd=page.evaluate("wallui.round")
    page.evaluate("cap=>{window.__cap=cap}",rnd["snapshot"]["capacity"])
    page.click("#wall-readybtn"); page.wait_for_timeout(500)
    print("countdown text:",page.inner_text("#wall-countdown"))
    pt=page.evaluate(BOARD_POINT,rnd["to_slot"]); print("target cell on screen:",pt["onscreen"], "slot",rnd["to_slot"])
    page.screenshot(path="/tmp/phone_round.png")
    tap(pt["x"],pt["y"]); page.wait_for_timeout(1500)
    print("result:",page.inner_text("#wall-result"))
    page.screenshot(path="/tmp/phone_result.png")
