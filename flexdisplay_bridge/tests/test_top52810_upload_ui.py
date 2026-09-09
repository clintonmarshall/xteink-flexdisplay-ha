"""Exercise actual inline event handlers without a browser or live device."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_upload_preview_send_state_machine():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the offline Studio event-handler test")
    html = (Path(__file__).parents[1] / "flexdisplay_bridge/static/dashboard-studio.html").read_text()
    script = html.split("<script>")[1].split("</script>")[0]
    handlers = script[script.index("    let tagImageRevision"):script.index("    activateWorkspace(state.workspace);")]
    harness = r"""
const vm = require("node:vm");
const assert = require("node:assert/strict");
const elements = {};
const $ = id => elements[id] ||= {value:"fit", disabled:false, files:[],
  listeners:{}, addEventListener(event, fn){this.listeners[event]=fn},
  removeAttribute(name){delete this[name]}};
let requests = [], confirm = true, pending = null;
const prepared = {image_base64:"native",logical_png_base64:"logical",stock_png_base64:"stock",
  plan_sha256:"a".repeat(64),sid:"A1B2C3",device_io:false,write_count:44};
const context = {$, window:{confirm:()=>confirm}, console,
  api:async (path, options) => {
    assert(!path.startsWith("/"));
    requests.push({path, options});
    if (pending) return await pending;
    return {json:async()=>path.includes("prepare") ? prepared : {job_id:"job-1",status:"pending"}};
  }};
vm.createContext(context);
vm.runInContext(SOURCE, context);
(async()=>{
  assert.equal(requests.length,0);
  $("#tagImageFile").files=[{size:100}];
  await $("#tagImagePreview").listeners.click();
  assert.equal(requests.length,1);
  assert.equal($("#tagImageSend").disabled,false);
  $("#tagImageMode").listeners.change();
  assert.equal($("#tagImageSend").disabled,true);
  assert.equal($("#tagImagePreviews").hidden,true);
  await $("#tagImageSend").listeners.click();
  assert.equal(requests.length,1);
  await $("#tagImagePreview").listeners.click();
  confirm=false;
  await $("#tagImageSend").listeners.click();
  assert.equal(requests.length,2);
  confirm=true;
  await $("#tagImageSend").listeners.click();
  await $("#tagImageSend").listeners.click();
  assert.equal(requests.length,3);
  assert.equal(requests[2].options.body.expected_plan_sha256,prepared.plan_sha256);
  assert.equal(requests[2].options.body.image_base64,"native");
  assert.equal(requests[2].options.body.address,"DF:84:6B:DE:F6:ED");
  let finish;
  pending=new Promise(resolve=>finish=resolve);
  const preview=$("#tagImagePreview").listeners.click();
  $("#tagImageFile").listeners.change();
  finish({json:async()=>prepared});
  await preview;
  assert.equal($("#tagImageSend").disabled,true);
  assert.equal($("#tagImagePreviews").hidden,true);
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    import json
    subprocess.run([node, "-e", "const SOURCE=" + json.dumps(handlers) + ";\n" + harness], check=True)
