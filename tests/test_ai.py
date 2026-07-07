"""AI feature tests — recommend, parse-habit, report."""


class TestAIRecommend:
    def test_recommend_vector_fallback(self, client, auth_headers):
        """Without LLM key, should return vector search results."""
        r = client.post('/ai/recommend', json={'goal': 'get healthier'}, headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data['success'] is True
        assert len(data['suggestions']) >= 1
        assert data['source'] == 'vector'

    def test_recommend_empty_goal(self, client, auth_headers):
        r = client.post('/ai/recommend', json={'goal': ''}, headers=auth_headers)
        assert r.status_code == 200
        assert r.get_json()['success'] is False

    def test_recommend_chinese_goal(self, client, auth_headers):
        r = client.post('/ai/recommend', json={'goal': '想减肥塑形'}, headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data['success'] is True
        assert len(data['suggestions']) >= 1


class TestAIParseHabit:
    def test_parse_without_llm_key(self, client, auth_headers):
        """Without LLM key, should return a graceful message."""
        r = client.post('/ai/parse-habit', json={'text': '每天早上跑步'}, headers=auth_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data['success'] is False
        assert 'LLM not configured' in data['message']

    def test_parse_empty_text(self, client, auth_headers):
        r = client.post('/ai/parse-habit', json={'text': ''}, headers=auth_headers)
        assert r.status_code == 200
        assert r.get_json()['success'] is False


class TestAIReport:
    def test_weekly_report(self, client, auth_headers):
        r = client.get('/ai/report?type=weekly&tone=coach')
        assert r.status_code == 200

    def test_monthly_report(self, client, auth_headers):
        r = client.get('/ai/report?type=monthly&tone=friend')
        assert r.status_code == 200

    def test_analyst_tone(self, client, auth_headers):
        r = client.get('/ai/report?type=weekly&tone=analyst')
        assert r.status_code == 200

    def test_report_with_data(self, client, auth_headers):
        """Report page when user has habits and check-ins."""
        client.post('/habits/new', json={
            'habit_name': 'Run', 'icon': 'fas fa-running',
            'frequency': 'Every day', 'time_period': 'Morning',
        }, headers=auth_headers)
        r = client.get('/ai/report?type=weekly&tone=coach')
        assert r.status_code == 200
