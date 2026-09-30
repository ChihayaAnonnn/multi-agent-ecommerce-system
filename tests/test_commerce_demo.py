"""页面输入策略必须与认证服务的注册契约一致。"""
from html.parser import HTMLParser
from pathlib import Path
from identity.passwords import MIN_PASSWORD_LENGTH,MAX_PASSWORD_LENGTH


def test_demo_password_bounds_match_registration_policy():
    class Inputs(HTMLParser):
        password=None
        def handle_starttag(self,tag,attrs):
            fields=dict(attrs)
            if tag=='input' and fields.get('id')=='password':
                self.password=fields
    parser=Inputs()
    parser.feed((Path(__file__).parents[1]/'demo'/'commerce.html').read_text())
    assert int(parser.password['minlength'])==MIN_PASSWORD_LENGTH
    assert int(parser.password['maxlength'])==MAX_PASSWORD_LENGTH
