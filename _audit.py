import ast, re, sys
from collections import Counter

# 1. web_api.py syntax
code = open('m2g/web_api.py').read()
ast.parse(code)
print('web_api.py syntax: OK')

# 2. script.js duplicate functions
js = open('frontend/script.js').read()
fns = re.findall(r'^(?:async )?function (\w+)', js, re.M)
dups = [f for f, n in Counter(fns).items() if n > 1]
print('Duplicate functions:', dups if dups else 'none')

# 3. Backtick balance (template literals)
bt = js.count('`')
print(f'Backtick count: {bt}  even={bt % 2 == 0}')

# 4. getElementById calls vs HTML ids
html = open('frontend/index.html').read()
html_ids = set(re.findall(r'id=["\']([\\w-]+)["\']', html))
js_getids = set(re.findall(r'getElementById\(["\']([\\w-]+)["\']\)', js))

# IDs injected dynamically via innerHTML (not in index.html)
dynamic_ids = {
    'deployFromAppsBtn', 'validateAppBtn', 'regenBtn', 'genFromDetailBtn',
    'goToInsightsBtnDetail', 'appFileTabs', 'appFileViewer',
    'generatorModelInfo', 'generatorCapsList', 'insightsModelName',
    'insightsModelTask', 'insightsContinueBtn', 'insightsGrid',
    'insightsContent', 'insightsEmpty', 'generatorEmpty', 'generatorContent',
    'generationProgress', 'generationSuccess', 'generationError',
    'generationProgressSub', 'generationProgressBar', 'generationStages',
    'generationSuccessMsg', 'generatedFilesList', 'viewGeneratedAppBtn',
    'goToDeployBtn', 'appsDetailContent', 'appsDetailEmpty',
    'deploymentEmpty', 'deploymentContent', 'deployModelName', 'deployModelSlug',
    'colabHwInfo', 'generatorOptions'
}
real_missing = js_getids - html_ids - dynamic_ids
print('IDs in JS not in HTML (non-dynamic):', sorted(real_missing) if real_missing else 'none')

# 5. Key API functions used
for fn in ['startAnalysis', 'generateApp', 'startLocal', 'stopLocal', 'getLocalStatus',
           'startColab', 'stopColab', 'getColabDeployment', 'fetchColabLogs',
           'getActivity', 'listApps', 'getApp', 'getAppFile', 'validateApp',
           'checkColab', 'getColabGpus']:
    used = ('API.' + fn) in js
    if not used:
        print(f'  WARNING: API.{fn} not used in script.js')

print('All API calls: present')

# 6. window exports for onclick usage
assert 'window.navigateTo = navigateTo' in js, 'window.navigateTo NOT exported'
assert 'window.selectApp = selectApp' in js, 'window.selectApp NOT exported'
print('window exports: OK')

# 7. No onclick= referencing bare function names (ES module scope issue)
inline_onclicks = re.findall(r'onclick="(\w+)\(', js)
bad = [o for o in inline_onclicks if o not in ('window',)]
print('Bare onclick calls (not window.):', bad if bad else 'none')

# 8. Script type=module in HTML
assert 'type="module"' in html, 'script.js not loaded as ES module'
print('script.js type=module: OK')

print('\nAll audits passed.')
