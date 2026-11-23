"""Check exact local credential values against candidate files and Git history.
Does not print values, matching content, tokens or private paths.
"""
import subprocess
from pathlib import Path
from dotenv import dotenv_values
values=dotenv_values('.env')
secrets=[v.encode() for k,v in values.items() if v and len(v)>=16 and any(x in k for x in ('TOKEN','KEY','PASSWORD','SECRET'))]
paths=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z']).split(b'\0')
violations=[]
for raw in paths:
    if not raw:continue
    p=Path(raw.decode())
    if p.is_file():
        data=p.read_bytes()
        if any(s in data for s in secrets):violations.append(str(p))
history=subprocess.check_output(['git','log','--all','-p','--no-ext-diff'])
if any(s in history for s in secrets):violations.append('git_history')
print('Exact credential matches:',len(violations))
for path in violations:print('File:',path)
raise SystemExit(bool(violations))
