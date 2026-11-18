"""Manual browser acceptance check against a running local app on port 8000."""
from pathlib import Path
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1100})
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto('http://127.0.0.1:8000/')
    page.wait_for_function("document.getElementById('rain-note').textContent.startsWith('Rainfall is not chosen')")
    assert page.locator('#barangay option').count()==27
    assert page.locator('#event-date').count()==0
    # Exercise actual map click, then exact keyboard coordinates for known land.
    page.locator('#map').click(position={'x':260,'y':190})
    page.locator('#latitude').fill('14.281')
    page.locator('#longitude').fill('121.398')
    page.get_by_role('button',name='Use coordinates').click()
    page.wait_for_function("document.getElementById('location-status').textContent.startsWith('Calios')")
    assert page.locator('#features .feature').count()==13
    for name,value in {'Age':'35','HouseMembers':'4','ChildrenCount':'1','ElderCount':'1','PWDCount':'0'}.items():
        page.locator('[name='+name+']').fill(value)
    page.locator('[name=HousingMaterial]').select_option('Concrete')
    page.get_by_role('button',name='Predict flood risk').click()
    page.locator('#result:not([hidden])').wait_for()
    assert 'risk in major storms' in page.locator('#result-title').inner_text()
    assert 'Across the 15 extreme rain events' in page.locator('#result-score').inner_text()
    assert page.locator('#event-table tbody tr').count()==60
    assert page.locator('#result-rules li').count()>=4
    page.screenshot(path=str(Path(__file__).parents[1]/'data'/'assessment_desktop.png'),full_page=True)
    page.locator('[name=Age]').fill('36')
    assert page.locator('#result').is_hidden()
    page.locator('#latitude').fill('0');page.locator('#longitude').fill('0')
    page.get_by_role('button',name='Use coordinates').click()
    page.wait_for_function("document.getElementById('location-status').textContent.includes('inside a Santa Cruz')")
    assert page.locator('#assess').is_disabled()
    assert all('Unavailable' in x for x in page.locator('#features .feature').all_text_contents())
    page.set_viewport_size({'width':390,'height':844})
    page.reload()
    page.wait_for_function("document.getElementById('rain-note').textContent.startsWith('Rainfall is not chosen')")
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(Path(__file__).parents[1]/'data'/'assessment_mobile.png'),full_page=True)
    assert not errors,errors
    print('PASS: map click, coordinate extraction, 26 barangays, household submission, 60-event AGAP NOAH v2.8 prediction and event table, stale-result clearing, outside-boundary rejection, mobile overflow, no JS errors.')
    browser.close()
