import hashlib, json, time
from app.config import RULE_VERSION
def make(text, parameters=None):
    digest=hashlib.sha256(text.encode('utf-8')).hexdigest()
    parameters=parameters or {}
    canonical=json.dumps(parameters,sort_keys=True,ensure_ascii=False,separators=(',',':'))
    ident=hashlib.sha256((RULE_VERSION+':'+digest+':'+canonical).encode('utf-8')).hexdigest()[:16]
    return {'run_id':ident, 'sha256':digest, 'rule_version':RULE_VERSION, 'parameters':parameters, 'created_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
