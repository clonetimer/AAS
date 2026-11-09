"""Test-only handler adapter for established business API contract suites.

Authentication itself is exercised through the unmodified production handler
in test_home_login_and_dashboard.py. These suites test preexisting business API
responses without introducing credentials into the test project.
"""
from scripts.visualization_server import DashboardHandler


class AuthorizedTestHandler(DashboardHandler):
    """Inject a synthetic identity only for isolated legacy API contract tests."""

    def authenticated_user(self, main):
        return 'automated-contract-test'
