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


def test_shopping_requirements_have_only_chat_input():
    class Controls(HTMLParser):
        in_shop=False
        controls=[]
        def handle_starttag(self,tag,attrs):
            fields=dict(attrs)
            if tag=='form':self.in_shop=fields.get('id')=='shop-form'
            if self.in_shop and tag in {'input','select','textarea'}:
                self.controls.append(fields.get('id'))
        def handle_endtag(self,tag):
            if tag=='form':self.in_shop=False
    parser=Controls()
    parser.feed((Path(__file__).parents[1]/'demo'/'commerce.html').read_text())
    assert parser.controls==['message']
