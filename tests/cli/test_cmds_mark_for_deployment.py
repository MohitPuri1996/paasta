# Copyright 2015-2016 Yelp Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import os

import mock
from mock import ANY
from mock import patch
from pytest import raises

from paasta_tools.cli.cmds import mark_for_deployment
from paasta_tools.slack import PaastaSlackClient


class FakeArgs:
    deploy_group = 'test_deploy_group'
    service = 'test_service'
    git_url = 'git://false.repo/services/test_services'
    commit = 'd670460b4b4aece5915caf5c68d12f560a9fe3e4'
    soa_dir = 'fake_soa_dir'
    block = False
    verbose = False
    auto_rollback = False
    verify_image = False
    timeout = 10.0
    auto_certify_delay = 1.0
    auto_abandon_delay = 1.0


@patch('paasta_tools.cli.cmds.mark_for_deployment.validate_service_name', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.mark_for_deployment', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.get_currently_deployed_sha', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.list_deploy_groups', autospec=True)
def test_paasta_mark_for_deployment_acts_like_main(
    mock_list_deploy_groups,
    mock_get_currently_deployed_sha,
    mock_mark_for_deployment,
    mock_validate_service_name,
):
    mock_list_deploy_groups.return_value = ['test_deploy_group']
    mock_mark_for_deployment.return_value = 42
    assert mark_for_deployment.paasta_mark_for_deployment(FakeArgs) == 42
    mock_mark_for_deployment.assert_called_once_with(
        service='test_service',
        deploy_group='test_deploy_group',
        commit='d670460b4b4aece5915caf5c68d12f560a9fe3e4',
        git_url='git://false.repo/services/test_services',
    )
    assert mock_validate_service_name.called


@patch('paasta_tools.cli.cmds.mark_for_deployment._log', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment._log_audit', autospec=True)
@patch('paasta_tools.remote_git.create_remote_refs', autospec=True)
def test_mark_for_deployment_happy(mock_create_remote_refs, mock__log_audit, mock__log):
    actual = mark_for_deployment.mark_for_deployment(
        git_url='fake_git_url',
        deploy_group='fake_deploy_group',
        service='fake_service',
        commit='fake_commit',
    )
    assert actual == 0
    mock_create_remote_refs.assert_called_once_with(
        git_url='fake_git_url',
        ref_mutator=ANY,
        force=True,
    )
    mock__log_audit.assert_called_once_with(
        action='mark-for-deployment',
        action_details={'deploy_group': 'fake_deploy_group', 'commit': 'fake_commit'},
        service='fake_service',
    )


@patch('paasta_tools.cli.cmds.mark_for_deployment._log', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment._log_audit', autospec=True)
@patch('paasta_tools.remote_git.create_remote_refs', autospec=True)
def test_mark_for_deployment_sad(mock_create_remote_refs, mock__log_audit, mock__log):
    mock_create_remote_refs.side_effect = Exception('something bad')
    with patch('time.sleep', autospec=True):
        actual = mark_for_deployment.mark_for_deployment(
            git_url='fake_git_url',
            deploy_group='fake_deploy_group',
            service='fake_service',
            commit='fake_commit',
        )
    assert actual == 1
    assert mock_create_remote_refs.call_count == 3
    assert not mock__log_audit.called


@patch('paasta_tools.cli.cmds.mark_for_deployment.validate_service_name', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.is_docker_image_already_in_registry', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.get_currently_deployed_sha', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.list_deploy_groups', autospec=True)
def test_paasta_mark_for_deployment_when_verify_image_fails(
    mock_list_deploy_groups,
    mock_get_currently_deployed_sha,
    mock_is_docker_image_already_in_registry,
    mock_validate_service_name,
):
    class FakeArgsRollback(FakeArgs):
        verify_image = True

    mock_list_deploy_groups.return_value = ['test_deploy_groups']
    mock_is_docker_image_already_in_registry.return_value = False
    with raises(ValueError):
        mark_for_deployment.paasta_mark_for_deployment(FakeArgsRollback)


@patch('paasta_tools.cli.cmds.mark_for_deployment.validate_service_name', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.is_docker_image_already_in_registry', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.get_currently_deployed_sha', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.list_deploy_groups', autospec=True)
def test_paasta_mark_for_deployment_when_verify_image_succeeds(
    mock_list_deploy_groups,
    mock_get_currently_deployed_sha,
    mock_is_docker_image_already_in_registry,
    mock_validate_service_name,
):
    class FakeArgsRollback(FakeArgs):
        verify_image = True

    mock_list_deploy_groups.return_value = ['test_deploy_groups']
    mock_is_docker_image_already_in_registry.return_value = False
    with raises(ValueError):
        mark_for_deployment.paasta_mark_for_deployment(FakeArgsRollback)
    mock_is_docker_image_already_in_registry.assert_called_with(
        'test_service',
        'fake_soa_dir',
        'd670460b4b4aece5915caf5c68d12f560a9fe3e4',
    )


@patch('paasta_tools.cli.cmds.mark_for_deployment.MarkForDeploymentProcess.run_timeout', new=1.0, autospec=False)
@patch('paasta_tools.cli.cmds.mark_for_deployment.get_slack_client', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.validate_service_name', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.mark_for_deployment', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.MarkForDeploymentProcess.do_wait_for_deployment', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.get_currently_deployed_sha', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.list_deploy_groups', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.load_system_paasta_config', autospec=True)
def test_paasta_mark_for_deployment_with_good_rollback(
    mock_load_system_paasta_config,
    mock_list_deploy_groups,
    mock_get_currently_deployed_sha,
    mock_do_wait_for_deployment,
    mock_mark_for_deployment,
    mock_validate_service_name,
    mock_get_slack_client,
):
    class FakeArgsRollback(FakeArgs):
        auto_rollback = True
        block = True
        timeout = 600

    mock_list_deploy_groups.return_value = ['test_deploy_groups']
    mock_mark_for_deployment.return_value = 0

    def do_wait_for_deployment_side_effect(self, target_commit):
        if target_commit == FakeArgs.commit:
            self.trigger('rollback_button_clicked')
        else:
            self.trigger('deploy_finished')

    mock_do_wait_for_deployment.side_effect = do_wait_for_deployment_side_effect

    def on_enter_rolled_back_side_effect(self):
        self.trigger('abandon_button_clicked')

    mock_get_currently_deployed_sha.return_value = "old-sha"
    with patch(
        'paasta_tools.cli.cmds.mark_for_deployment.MarkForDeploymentProcess.periodically_update_slack',
        autospec=True,
    ), patch(
        'paasta_tools.cli.cmds.mark_for_deployment.MarkForDeploymentProcess.on_enter_rolled_back',
        autospec=True,
        wraps=mark_for_deployment.MarkForDeploymentProcess.on_enter_rolled_back,
        side_effect=on_enter_rolled_back_side_effect,
    ):
        assert mark_for_deployment.paasta_mark_for_deployment(FakeArgsRollback) == 1
    print(mock_mark_for_deployment.mock_calls)
    mock_mark_for_deployment.assert_any_call(
        service='test_service',
        deploy_group='test_deploy_group',
        commit='d670460b4b4aece5915caf5c68d12f560a9fe3e4',
        git_url='git://false.repo/services/test_services',
    )
    mock_mark_for_deployment.assert_any_call(
        service='test_service',
        deploy_group='test_deploy_group',
        commit='old-sha',
        git_url='git://false.repo/services/test_services',
    )
    assert mock_mark_for_deployment.call_count == 2

    mock_do_wait_for_deployment.assert_any_call(
        mock.ANY,
        target_commit='d670460b4b4aece5915caf5c68d12f560a9fe3e4',
    )
    mock_do_wait_for_deployment.assert_any_call(
        mock.ANY,
        target_commit='old-sha',
    )
    assert mock_do_wait_for_deployment.call_count == 2


@patch('paasta_tools.cli.cmds.mark_for_deployment.get_slack_client', autospec=True)
@patch('paasta_tools.remote_git.get_authors', autospec=True)
def test_slack_deploy_notifier(mock_get_authors, mock_client):
    fake_psc = mock.create_autospec(PaastaSlackClient)
    fake_psc.post.return_value = [{'ok': True, 'message': {'ts': 1234}}]
    mock_client.return_value = fake_psc
    mock_get_authors.return_value = 0, "fakeuser1 fakeuser2"
    sdn = mark_for_deployment.SlackDeployNotifier(
        service='testservice',
        deploy_info={
            'pipeline':
            [
                {'step': 'test_deploy_group', 'slack_notify': True, },
            ],
            'slack_channels': ['#webcore', '#webcore2'],
        },
        deploy_group='test_deploy_group',
        commit='newcommit',
        old_commit='oldcommit',
        git_url="foo",
    )
    assert sdn.notify_after_mark(ret=1) is None
    assert sdn.notify_after_mark(ret=0) is None
    assert sdn.notify_after_good_deploy() is None
    assert sdn.notify_after_auto_rollback() is None
    assert sdn.notify_after_abort() is None
    assert fake_psc.post.call_count > 0, fake_psc.post.call_args

    with mock.patch.dict(
        os.environ,
        {'BUILD_URL': 'https://www.yelp.com'},
        clear=True,
    ):
        assert sdn.get_url_message() == '<https://www.yelp.com/consoleFull|Jenkins Job>'


@patch('paasta_tools.cli.cmds.mark_for_deployment.get_slack_client', autospec=True)
@patch('paasta_tools.remote_git.get_authors', autospec=True)
def test_slack_deploy_notifier_with_auto_rollbacks(mock_get_authors, mock_client):
    fake_psc = mock.create_autospec(PaastaSlackClient)
    fake_psc.post.return_value = [{'ok': True, 'message': {'ts': 1234}}]
    mock_client.return_value = fake_psc
    mock_get_authors.return_value = 0, "fakeuser1 fakeuser2"
    sdn = mark_for_deployment.SlackDeployNotifier(
        service='testservice',
        deploy_info={
            'pipeline':
            [
                {'step': 'test_deploy_group', 'slack_notify': True, },
            ],
            'slack_channels': ['#webcore', '#webcore2'],
        },
        deploy_group='test_deploy_group',
        commit='newcommit',
        old_commit='oldcommit',
        git_url="foo",
        auto_rollback=True,
    )
    assert sdn.notify_after_mark(ret=1) is None
    assert sdn.notify_after_mark(ret=0) is None
    assert sdn.notify_after_good_deploy() is None
    assert sdn.notify_after_auto_rollback() is None
    assert sdn.notify_after_abort() is None
    assert fake_psc.post.call_count > 0, fake_psc.post.call_args


@patch('paasta_tools.cli.cmds.mark_for_deployment.get_slack_client', autospec=True)
@patch('paasta_tools.remote_git.get_authors', autospec=True)
def test_slack_deploy_notifier_on_non_notify_groups(mock_get_authors, mock_client):
    fake_psc = mock.create_autospec(PaastaSlackClient)
    mock_client.return_value = fake_psc
    mock_get_authors.return_value = 1, "fakeuser1 fakeuser2"
    sdn = mark_for_deployment.SlackDeployNotifier(
        service='testservice',
        deploy_info={
            'pipeline':
            [
                {'step': 'test_deploy_group', 'slack_notify': False, },
            ],
        },
        deploy_group='test_deploy_group',
        commit='newcommit',
        old_commit='oldcommit',
        git_url="foo",
    )
    assert sdn.notify_after_mark(ret=1) is None
    assert sdn.notify_after_mark(ret=0) is None
    assert sdn.notify_after_good_deploy() is None
    assert sdn.notify_after_auto_rollback() is None
    assert sdn.notify_after_abort() is None
    assert fake_psc.post.call_count == 0, fake_psc.post.call_args


@patch('paasta_tools.cli.cmds.mark_for_deployment.get_slack_client', autospec=True)
@patch('paasta_tools.remote_git.get_authors', autospec=True)
def test_slack_deploy_notifier_doesnt_notify_on_same_commit(mock_get_authors, mock_client):
    fake_psc = mock.create_autospec(PaastaSlackClient)
    mock_client.return_value = fake_psc
    mock_get_authors.return_value = 0, "fakeuser1 fakeuser2"
    sdn = mark_for_deployment.SlackDeployNotifier(
        service='testservice',
        deploy_info={
            'pipeline':
            [
                {'step': 'test_deploy_group', 'slack_notify': True, },
            ],
            'slack_channels': ['#webcore', '#webcore2'],
        },
        deploy_group='test_deploy_group',
        commit='samecommit',
        old_commit='samecommit',
        git_url="foo",
    )
    assert sdn.notify_after_mark(ret=1) is None
    assert sdn.notify_after_mark(ret=0) is None
    assert sdn.notify_after_good_deploy() is None
    assert sdn.notify_after_auto_rollback() is None
    assert sdn.notify_after_abort() is None
    assert fake_psc.post.call_count == 0, fake_psc.post.call_args


@patch('paasta_tools.cli.cmds.mark_for_deployment.get_slack_client', autospec=True)
@patch('paasta_tools.remote_git.get_authors', autospec=True)
def test_slack_deploy_notifier_notifies_on_deploy_info_flags(mock_get_authors, mock_client):
    fake_psc = mock.create_autospec(PaastaSlackClient)
    fake_psc.post.return_value = [{'ok': True, 'message': {'ts': 1234}}]
    mock_client.return_value = fake_psc
    mock_get_authors.return_value = 0, "fakeuser1 fakeuser2"
    sdn = mark_for_deployment.SlackDeployNotifier(
        service='testservice',
        deploy_info={
            'pipeline': [
                {
                    'step': 'test_deploy_group',
                    'notify_after_mark': True,
                    'notify_after_good_deploy': True,
                    'notify_after_auto_rollback': True,
                    'notify_after_abort': True,
                },
            ],
            'slack_channels': ['#webcore', '#webcore2'],
        },
        deploy_group='test_deploy_group',
        commit='newcommit',
        old_commit='oldcommit',
        git_url="foo",
    )
    assert sdn.notify_after_mark(ret=1) is None
    assert sdn.notify_after_mark(ret=0) is None
    assert sdn.notify_after_good_deploy() is None
    assert sdn.notify_after_auto_rollback() is None
    assert sdn.notify_after_abort() is None
    assert fake_psc.post.call_count > 0, fake_psc.post.call_args
    assert "Jenkins" or "Run by" in sdn.get_url_message()


@patch('paasta_tools.cli.cmds.mark_for_deployment.get_slack_client', autospec=True)
@patch('paasta_tools.remote_git.get_authors', autospec=True)
def test_slack_deploy_notifier_doesnt_notify_on_deploy_info_flags(mock_get_authors, mock_client):
    fake_psc = mock.create_autospec(PaastaSlackClient)
    mock_client.return_value = fake_psc
    mock_get_authors.return_value = 0, "fakeuser1 fakeuser2"
    sdn = mark_for_deployment.SlackDeployNotifier(
        service='testservice',
        deploy_info={
            'pipeline':
            [
                {
                    'step': 'test_deploy_group',
                    'slack_notify': True,
                    'notify_after_mark': False,
                    'notify_after_good_deploy': False,
                    'notify_after_auto_rollback': False,
                    'notify_after_abort': False,
                },
            ],
        },
        deploy_group='test_deploy_group',
        commit='newcommit',
        old_commit='oldcommit',
        git_url="foo",
    )
    assert sdn.notify_after_mark(ret=1) is None
    assert sdn.notify_after_mark(ret=0) is None
    assert sdn.notify_after_good_deploy() is None
    assert sdn.notify_after_auto_rollback() is None
    assert sdn.notify_after_abort() is None
    assert fake_psc.post.call_count == 0, fake_psc.post.call_args


@patch('paasta_tools.cli.cmds.mark_for_deployment.MarkForDeploymentProcess.periodically_update_slack', autospec=True)
@patch('paasta_tools.remote_git.get_authors', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.get_slack_client', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.mark_for_deployment', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.wait_for_deployment', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.load_system_paasta_config', autospec=True)
def test_MarkForDeployProcess_handles_wait_for_deployment_failure(
    mock_load_system_paasta_config,
    mock_wait_for_deployment,
    mock_mark_for_deployment,
    mock_get_slack_client,
    mock_get_authors,
    mock_periodically_update_slack,
):
    mock_get_authors.return_value = 0, "fakeuser1 fakeuser2"
    mfdp = mark_for_deployment.MarkForDeploymentProcess(
        service='service',
        block=True,
        auto_rollback=True,

        deploy_info=None,
        deploy_group=None,
        commit='abc123432u49',
        old_git_sha='abc123455',
        git_url=None,
        soa_dir=None,
        timeout=None,
        auto_certify_delay=1,
        auto_abandon_delay=1,
    )

    mock_mark_for_deployment.return_value = 0
    mock_wait_for_deployment.side_effect = Exception()

    retval = mfdp.run()

    assert mock_mark_for_deployment.call_count == 1
    assert mock_wait_for_deployment.call_count == 1
    assert mfdp.state == 'deploy_errored'
    assert retval == 2


@patch('paasta_tools.cli.cmds.mark_for_deployment.MarkForDeploymentProcess.periodically_update_slack', autospec=True)
@patch('paasta_tools.remote_git.get_authors', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.get_slack_client', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.mark_for_deployment', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.wait_for_deployment', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.load_system_paasta_config', autospec=True)
def test_MarkForDeployProcess_handles_wait_for_deployment_cancelled(
    mock_load_system_paasta_config,
    mock_wait_for_deployment,
    mock_mark_for_deployment,
    mock_get_slack_client,
    mock_get_authors,
    mock_periodically_update_slack,
):
    mock_get_authors.return_value = 0, "fakeuser1 fakeuser2"
    mfdp = mark_for_deployment.MarkForDeploymentProcess(
        service='service',
        block=True,
        # For this test, auto_rollback must be True so that the deploy_cancelled trigger takes us to start_rollback
        # instead of deploy_errored.
        auto_rollback=True,

        deploy_info=None,
        deploy_group=None,
        commit='abc123512',
        old_git_sha='asgdser23',
        git_url=None,
        soa_dir=None,
        timeout=None,
        auto_certify_delay=1,
        auto_abandon_delay=1,
    )

    mock_mark_for_deployment.return_value = 0
    mock_wait_for_deployment.side_effect = KeyboardInterrupt()

    retval = mfdp.run()

    assert mock_mark_for_deployment.call_count == 1
    assert retval == 1
    assert mfdp.state == 'deploy_cancelled'


@patch('paasta_tools.remote_git.get_authors', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.Thread', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.get_slack_client', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.mark_for_deployment', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.wait_for_deployment', autospec=True)
@patch('paasta_tools.automatic_rollbacks.slack.get_slack_events', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.load_system_paasta_config', autospec=True)
def test_MarkForDeployProcess_skips_wait_for_deployment_when_block_is_False(
    mock_load_system_paasta_config,
    mock_get_slack_events,
    mock_wait_for_deployment,
    mock_mark_for_deployment,
    mock_get_slack_client,
    mock_Thread,
    mock_get_authors,
):
    mock_get_authors.return_value = 0, "fakeuser1 fakeuser2"
    mfdp = mark_for_deployment.MarkForDeploymentProcess(
        service='service',
        block=False,
        auto_rollback=False,

        deploy_info=None,
        deploy_group=None,
        commit='abc123456789',
        old_git_sha='oldsha1234',
        git_url=None,
        soa_dir=None,
        timeout=None,
        auto_certify_delay=1,
        auto_abandon_delay=1,
    )

    mock_mark_for_deployment.return_value = 0
    mock_wait_for_deployment.side_effect = Exception()

    retval = mfdp.run()

    assert mock_mark_for_deployment.call_count == 1
    assert mock_wait_for_deployment.call_count == 0
    assert retval == 0
    assert mfdp.state == 'deploying'


@patch('paasta_tools.cli.cmds.mark_for_deployment.MarkForDeploymentProcess.periodically_update_slack', autospec=True)
@patch('paasta_tools.remote_git.get_authors', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.get_slack_client', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.mark_for_deployment', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.wait_for_deployment', autospec=True)
@patch('paasta_tools.cli.cmds.mark_for_deployment.load_system_paasta_config', autospec=True)
def test_MarkForDeployProcess_goes_to_mfd_failed_when_mark_for_deployment_fails(
    mock_load_system_paasta_config,
    mock_wait_for_deployment,
    mock_mark_for_deployment,
    mock_get_slack_client,
    mock_get_authors,
    mock_periodically_update_slack,
):
    mock_get_authors.return_value = 0, "fakeuser1 fakeuser2"
    mfdp = mark_for_deployment.MarkForDeploymentProcess(
        service='service',
        block=False,  # shouldn't matter for this test
        auto_rollback=False,  # shouldn't matter for this test

        deploy_info=None,
        deploy_group=None,
        commit='asbjkslerj',
        old_git_sha='abscerwerr',
        git_url=None,
        soa_dir=None,
        timeout=None,
        auto_certify_delay=1,
        auto_abandon_delay=1,
    )

    mock_mark_for_deployment.return_value = 1
    mock_wait_for_deployment.side_effect = Exception()

    retval = mfdp.run()

    assert mock_mark_for_deployment.call_count == 1
    assert mock_wait_for_deployment.call_count == 0
    assert retval == 1
    assert mfdp.state == 'mfd_failed'
