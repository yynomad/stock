"""Run the production SDKs against in-memory HTTP responses."""
from contextlib import contextmanager
import json
import unittest
from unittest.mock import patch

import httpx
import httpx2
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool

from stockman.model_providers import DEFAULT_URLS, ModelEndpoint, endpoint_url


@tool
def lookup(symbol: str) -> str:
    """Find evidence for a stock."""
    return 'synthetic evidence for ' + symbol


@contextmanager
def intercept(provider, handle):
    if provider == 'gemini':
        def send(client, request):
            return handle(httpx, request)
        with patch.object(httpx.Client, '_send_single_request', autospec=True, side_effect=send):
            yield
    else:
        def client(**options):
            return httpx2.Client(transport=httpx2.MockTransport(lambda req: handle(httpx2, req)), **options)
        with patch('openai.DefaultHttpxClient', side_effect=client):
            yield


def reply(provider, first):
    if provider == 'gemini':
        parts = [{'functionCall': {'name': 'lookup', 'args': {'symbol': 'AAPL'}}}] if first else [{'text': '{"events":[]}'}]
        return {'candidates': [{'content': {'role': 'model', 'parts': parts}, 'finishReason': 'STOP'}],
                'usageMetadata': {'promptTokenCount': 11, 'candidatesTokenCount': 4, 'totalTokenCount': 15}}
    call = {'id':'call-fixture','type':'function','function':{'name':'lookup','arguments':'{"symbol":"AAPL"}'}}
    message = {'role':'assistant','content':None,'tool_calls':[call]} if first else {'role':'assistant','content':'{"events":[]}'}
    return {'id':'fixture','created':1,'model':'market-alias','object':'chat.completion',
            'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if first else 'stop'}],
            'usage':{'prompt_tokens':11,'completion_tokens':4,'total_tokens':15,'prompt_cache_hit_tokens':2}}


class ModelProviderTest(unittest.TestCase):
    def test_url_policy_and_search_key_isolation(self):
        for invalid in ('http://bad.invalid', 'https://user:private@bad.invalid', 'https://bad.invalid?key=private',
                        'https://bad.invalid/#private','https://bad.invalid:xyz','https://bad.invalid\\@other.invalid'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError) as exc:
                endpoint_url(invalid)
            self.assertNotIn('private',str(exc.exception))
        model = ModelEndpoint.from_environment(env={'STOCKMAN_MODEL_PROVIDER':'deepseek','DEEPSEEK_API_KEY':'fixture-model',
                                                    'GEMINI_API_KEY':'fixture-google'})
        self.assertEqual(model.search_key,'')
        self.assertNotIn('fixture-model',repr(model))
        self.assertEqual(ModelEndpoint('gemini','fixture','fixture-model','https://gateway.invalid').search_key,'')
        self.assertEqual(ModelEndpoint('gemini','fixture','fixture-model',DEFAULT_URLS['gemini']).search_key,'fixture-model')

    def test_provider_default_and_grounding_use_distinct_credentials(self):
        from stockman.service_config import ModelConfig
        endpoint = ModelConfig(key='fixture-deepseek-key',provider='deepseek',name='deepseek-flash').endpoint
        self.assertEqual(endpoint.base_url,DEFAULT_URLS['deepseek'])
        with self.assertRaisesRegex(ValueError,'requires a separate API key') as exc:
            ModelEndpoint('deepseek','deepseek-flash','fixture-private-key',DEFAULT_URLS['deepseek'],'fixture-private-key')
        self.assertNotIn('fixture-private-key',str(exc.exception))
        with self.assertRaisesRegex(ValueError,'separate from URL'):
            ModelEndpoint('gemini','fixture','fixture-private-key','https://gateway.invalid/fixture-private-key')

    def test_both_sdks_roundtrip_on_configured_gateway_with_usage(self):
        for provider in ('gemini','deepseek'):
            requests = []
            def handle(http, request):
                requests.append(request)
                return http.Response(200,json=reply(provider,len(requests)==1),request=request)
            with self.subTest(provider=provider), intercept(provider, handle):
                endpoint=ModelEndpoint(provider,'market-alias','fixture-secret','https://gateway.invalid/llm')
                model=endpoint.create()
                question=HumanMessage('Question for the model')
                bound=model.bind_tools([lookup])
                first=bound.invoke([question])
                call,=first.tool_calls
                self.assertEqual(call['name'],'lookup')
                result=bound.invoke([question,first,ToolMessage(lookup.invoke(call['args']),tool_call_id=call['id'],name='lookup')])
                self.assertEqual(result.content,'{"events":[]}')
                self.assertEqual(result.usage_metadata['input_tokens'],11)
                self.assertEqual(result.usage_metadata['output_tokens'],4)
                self.assertEqual(len(requests),2)
                for request in requests:
                    self.assertTrue(str(request.url).startswith('https://gateway.invalid/llm/'))
                    self.assertNotIn(b'fixture-secret',request.content)
                    key=request.headers.get('x-goog-api-key') if provider=='gemini' else request.headers.get('authorization')
                    self.assertIn('fixture-secret',key)
                self.assertIn('synthetic evidence for AAPL',requests[1].content.decode())
                if provider=='deepseek':
                    body=json.loads(requests[1].content)
                    self.assertEqual(body['messages'][-1]['tool_call_id'],'call-fixture')
                    self.assertEqual(result.usage_metadata['input_token_details']['cache_read'],2)

    def test_configured_api_does_not_follow_redirects(self):
        for provider in ('gemini','deepseek'):
            urls=[]
            def handle(http, request):
                urls.append(str(request.url))
                if len(urls)>1:
                    raise AssertionError('followed redirect')
                return http.Response(307,headers={'Location':'https://untrusted.invalid/secret'},json={'error':{'message':'redirect'}},request=request)
            with self.subTest(provider=provider),intercept(provider,handle):
                with self.assertRaises(Exception):
                    ModelEndpoint(provider,'market-alias','fixture-secret','https://gateway.invalid').create().invoke('hello')
                self.assertEqual(len(urls),1)

    def test_official_deepseek_uses_json_mode_and_disables_thinking(self):
        def handle(http, request):
            self.assertEqual(request.url.host,'api.deepseek.com')
            body=json.loads(request.content)
            self.assertEqual(body['thinking'],{'type':'disabled'})
            self.assertEqual(body['response_format'],{'type':'json_object'})
            return http.Response(200,json=reply('deepseek',False),request=request)
        with intercept('deepseek',handle):
            model=ModelEndpoint('deepseek','deepseek-flash','fixture-secret',DEFAULT_URLS['deepseek']).create(json_output=True)
            self.assertEqual(model.invoke('Return JSON.').content,'{"events":[]}')
