const fs = require('fs'), path = require('path'), assert = require('assert');
const runtime = process.env.PLAYWRIGHT_MODULE || 'playwright';
const { chromium } = require(runtime);
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.BROWSER_EXECUTABLE || undefined, headless: true });
  try {
    fs.mkdirSync(path.join(__dirname, 'results'), {recursive:true});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    let html = fs.readFileSync(path.join(__dirname, '../src/ps/camera_web/review.html'), 'utf8');
    html = html.replace('  boot();', `
      window.renderPresence = function(validation, busy=false) {
        connected=true; lastStateAt=Date.now();
        state={session_id:'test-photo',workflow_state:'AWAITING_IMAGE_REVIEW',busy,phase:busy?'PROCESSING':'AWAITING_IMAGE_REVIEW',
               report_ready:false,image_validation:validation,camera_active:false,
               error:validation?.message || ''};
        setPhase(derivePhase(state)); renderConfirm(); renderError(); syncButtons();
        return { phase, retakeDisabled:el.btnRetake2.disabled, reportReady:state.report_ready };
      };`);
    await page.setContent(html);
    let state = await page.evaluate(() => window.renderPresence({status:'no_eye', detected:false,
      message:'未识别到眼睛，请调整摄像头后重新拍摄。'}));
    assert.equal(state.phase, 'confirm');
    assert.equal(state.retakeDisabled, false);
    assert.equal(state.reportReady, false);
    assert.equal(await page.locator('#confirmTitle').textContent(), '未识别到眼睛');
    assert(await page.locator('#eyePresenceNotice').isVisible());
    assert((await page.locator('#bannerMsg').textContent()).includes('未识别到眼睛'));
    await page.screenshot({path: path.join(__dirname, 'results/eye_presence_ui.png'), fullPage:true});
    await page.evaluate(() => window.renderPresence(null));
    assert.equal(await page.locator('#confirmTitle').textContent(), '照片已采集');
    assert(!(await page.locator('#eyePresenceNotice').isVisible()));
    assert(await page.locator('#banner').isHidden());
    state = await page.evaluate(() => window.renderPresence(null, true));
    assert.equal(state.retakeDisabled, true);
    assert.equal(state.phase, 'analyzing');
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(__dirname, 'results/eye_presence_ui.json'), JSON.stringify({
      passed:true, checks:['no-eye-message', 'retake-enabled', 'no-report', 'new-photo-clears-message', 'busy-disables-retake', 'processing-shows-progress'], errors
    }, null, 2));
    console.log('Eye-presence UI checks passed');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode=1; });
