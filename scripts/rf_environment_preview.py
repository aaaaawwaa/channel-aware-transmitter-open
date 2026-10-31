"""Render S2 controls with synthetic unit-test IQ on a fake LCD; no board access."""
import argparse
import dataclasses
import json
import sys
import time
from pathlib import Path
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'pynq_overlay/sw/tests'))
sys.path.insert(0,str(ROOT/'pynq_overlay/sw'))
sys.path.insert(0,str(ROOT))
import test_rf_environment_pipeline as P
import test_ui_view as VIEW


def render(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    c,w,a,f=P.prepared(output)
    try:
        P.capture(w,a);a.rx_subpage='environment';a.page=VIEW.M.PAGE_FUTURE
        v=VIEW.V.UiView(VIEW.FakeLcd(),a)
        v.render()
        a16=v.lcd.array.astype(np.uint32) if hasattr(v,'lcd') else v.ui.lcd.array.astype(np.uint32)
        rgb=np.stack((((a16>>11)&31)*255//31,((a16>>5)&63)*255//63,(a16&31)*255//31),axis=-1)
        Image.fromarray(rgb.astype('uint8'),'RGB').save(output/'environment.png')
        widgets=[b for b in v.page_widgets[VIEW.M.PAGE_FUTURE] if getattr(b,'visible',False)]
        overlaps=[]
        for i,b in enumerate(widgets):
            for q in widgets[i+1:]:
                x0,y0,x1,y1=b.box;u0,v0,u1,v1=q.box
                if min(x1,u1)>max(x0,u0) and min(y1,v1)>max(y0,v0):
                    overlaps.append([getattr(b,'text',type(b).__name__),getattr(q,'text',type(q).__name__)])
        result=dict(scope='fake LCD with synthetic unit-test frame; not real screen acceptance',
            visible_widget_count=len(widgets),overlaps=overlaps,
            diagnostic=a.rx_environment_summary(),image='environment.png')
        (output/'receipt.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
        return result
    finally:w.stop()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();print(json.dumps(render(a.output),indent=2))
