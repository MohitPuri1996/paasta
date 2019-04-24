#!/usr/bin/env python
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
"""
PaaSTA service instance status/start/stop etc.
"""
import asyncio
import datetime
import logging
import traceback
from typing import Any
from typing import Dict
from typing import Mapping
from typing import MutableMapping
from typing import Optional
from typing import Sequence

import a_sync
import marathon
from kubernetes.client import V1Pod
from pyramid.response import Response
from pyramid.view import view_config

from paasta_tools import chronos_tools
from paasta_tools import flinkcluster_tools
from paasta_tools import kubernetes_tools
from paasta_tools import marathon_tools
from paasta_tools import tron_tools
from paasta_tools.api import settings
from paasta_tools.api.views.exception import ApiFailure
from paasta_tools.autoscaling.autoscaling_service_lib import get_autoscaling_info
from paasta_tools.cli.cmds.status import get_actual_deployments
from paasta_tools.marathon_serviceinit import get_marathon_dashboard_links  # TODO
from paasta_tools.marathon_serviceinit import get_short_task_id  # TODO
from paasta_tools.mesos_tools import get_all_slaves_for_blacklist_whitelist
from paasta_tools.mesos_tools import get_cached_list_of_not_running_tasks_from_frameworks
from paasta_tools.mesos_tools import get_cached_list_of_running_tasks_from_frameworks
from paasta_tools.mesos_tools import get_cpu_usage
from paasta_tools.mesos_tools import get_mem_usage
from paasta_tools.mesos_tools import get_mesos_slaves_grouped_by_attribute
from paasta_tools.mesos_tools import get_running_tasks_from_frameworks
from paasta_tools.mesos_tools import get_short_hostname_from_task
from paasta_tools.mesos_tools import get_task
from paasta_tools.mesos_tools import get_tasks_from_app_id
from paasta_tools.mesos_tools import select_tasks_by_id
from paasta_tools.mesos_tools import TaskNotFound
from paasta_tools.paasta_serviceinit import get_deployment_version
from paasta_tools.smartstack_tools import backend_is_up
from paasta_tools.smartstack_tools import get_backends
from paasta_tools.smartstack_tools import match_backends_and_tasks
from paasta_tools.utils import NoConfigurationForServiceError
from paasta_tools.utils import NoDockerImageError
from paasta_tools.utils import validate_service_instance
log = logging.getLogger(__name__)


def chronos_instance_status(
    instance_status: Mapping[str, Any],
    service: str,
    instance: str,
    verbose: bool,
) -> Mapping[str, Any]:
    cstatus: Dict[str, Any] = {}
    chronos_config = chronos_tools.load_chronos_config()
    client = chronos_tools.get_chronos_client(chronos_config)
    job_config = chronos_tools.load_chronos_job_config(
        service=service,
        instance=instance,
        cluster=settings.cluster,
        soa_dir=settings.soa_dir,
    )
    cstatus['desired_state'] = job_config.get_desired_state()
    job_type = chronos_tools.get_job_type(job_config.config_dict)
    if job_type == chronos_tools.JobType.Scheduled:
        schedule_type = 'schedule'
        schedule = job_config.get_schedule()
        epsilon = job_config.get_epsilon()
        time_zone = job_config.get_schedule_time_zone()
        if time_zone == 'null' or time_zone is None:
            time_zone = 'UTC'
        cstatus['schedule'] = {}
        cstatus['schedule']['schedule'] = schedule
        cstatus['schedule']['epsilon'] = epsilon
        cstatus['schedule']['time_zone'] = time_zone
    elif job_type == chronos_tools.JobType.Dependent:
        schedule_type = 'parents'
        parents = job_config.get_parents()
        cstatus['parents'] = parents
    else:
        schedule_type = 'unknown'
    cstatus['schedule_type'] = schedule_type
    cstatus['status'] = {}
    if verbose:
        running_task_count = len(
            select_tasks_by_id(
                a_sync.block(get_cached_list_of_running_tasks_from_frameworks),
                job_config.get_job_name(),
            ),
        )
        cstatus['status']['mesos_state'] = 'running' if running_task_count else 'not_running'
    cstatus['status']['disabled_state'] = 'not_scheduled' if job_config.get_disabled() else 'scheduled'
    cstatus['status']['chronos_state'] = chronos_tools.get_chronos_status_for_job(client, service, instance)
    cstatus['command'] = job_config.get_cmd()
    last_time, last_status = chronos_tools.get_status_last_run(job_config.config_dict)
    if last_status == chronos_tools.LastRunState.Success:
        last_status = 'success'
    elif last_status == chronos_tools.LastRunState.Fail:
        last_status = 'fail'
    elif last_status == chronos_tools.LastRunState.NotRun:
        last_status = 'not_run'
    else:
        last_status = ''
    if last_status == 'not_run' or last_status == '':
        last_time = 'never'
    cstatus['last_status'] = {}
    cstatus['last_status']['result'] = last_status
    cstatus['last_status']['time'] = last_time

    return cstatus


def tron_instance_status(
    instance_status: Mapping[str, Any],
    service: str,
    instance: str,
    verbose: bool,
) -> Mapping[str, Any]:
    status: Dict[str, Any] = {}
    client = tron_tools.get_tron_client()
    short_job, action = instance.split('.')
    job = f"{service}.{short_job}"

    job_content = client.get_job_content(job=job)
    latest_run_id = client.get_latest_job_run_id(job_content=job_content)
    action_run = client.get_action_run(job=job, action=action, run_id=latest_run_id)

    # job info
    status['job_name'] = short_job
    status['job_status'] = job_content['status']
    status['job_schedule'] = '{} {}'.format(job_content['scheduler']['type'], job_content['scheduler']['value'])
    status['job_url'] = tron_tools.get_tron_dashboard_for_cluster(settings.cluster) + f'#job/{job}'

    if action:
        status['action_name'] = action
    if action_run['state']:
        status['action_state'] = action_run['state']
    if action_run['start_time']:
        status['action_start_time'] = action_run['start_time']
    if action_run['raw_command']:
        status['action_raw_command'] = action_run['raw_command']
    if action_run['stdout']:
        status['action_stdout'] = '\n'.join(action_run['stdout'])
    if action_run['stderr']:
        status['action_stderr'] = '\n'.join(action_run['stderr'])
    if action_run['command']:
        status['action_command'] = action_run['command']

    return status


def adhoc_instance_status(
    instance_status: Mapping[str, Any],
    service: str,
    instance: str,
    verbose: bool,
) -> Mapping[str, Any]:
    cstatus: Dict[str, Any] = {}
    return cstatus


def flinkcluster_instance_status(
    instance_status: Mapping[str, Any],
    service: str,
    instance: str,
    verbose: bool,
) -> Optional[Mapping[str, Any]]:
    status: Optional[Mapping[str, Any]] = None
    client = settings.kubernetes_client
    if client is not None:
        status = flinkcluster_tools.get_flinkcluster_config(
            kube_client=client,
            service=service,
            instance=instance,
        )
    return status


def kubernetes_instance_status(
    instance_status: Mapping[str, Any],
    service: str,
    instance: str,
    verbose: bool,
) -> Mapping[str, Any]:
    kstatus: Dict[str, Any] = {}
    job_config = kubernetes_tools.load_kubernetes_service_config(
        service, instance, settings.cluster, soa_dir=settings.soa_dir,
    )
    client = settings.kubernetes_client
    if client is not None:
        # bouncing status can be inferred from app_count, ref get_bouncing_status
        pod_list = kubernetes_tools.pods_for_service_instance(job_config.service, job_config.instance, client)
        active_shas = kubernetes_tools.get_active_shas_for_service(pod_list)
        kstatus['app_count'] = max(
            len(active_shas['config_sha']),
            len(active_shas['git_sha']),
        )
        kstatus['desired_state'] = job_config.get_desired_state()
        kstatus['bounce_method'] = kubernetes_tools.KUBE_DEPLOY_STATEGY_REVMAP[job_config.get_bounce_method()]
        kubernetes_job_status(kstatus=kstatus, client=client, job_config=job_config, verbose=verbose, pod_list=pod_list)
    return kstatus


def kubernetes_job_status(
    kstatus: MutableMapping[str, Any],
    client: kubernetes_tools.KubeClient,
    job_config: kubernetes_tools.KubernetesDeploymentConfig,
    pod_list: Sequence[V1Pod],
    verbose: bool,
) -> None:
    app_id = job_config.get_sanitised_deployment_name()
    kstatus['app_id'] = app_id
    if verbose is True:
        kstatus['slaves'] = [
            pod.spec.node_name
            for pod in pod_list
        ]
    kstatus['expected_instance_count'] = job_config.get_instances()

    app = kubernetes_tools.get_kubernetes_app_by_name(app_id, client)
    deploy_status = kubernetes_tools.get_kubernetes_app_deploy_status(client, app, job_config.get_instances())
    kstatus['deploy_status'] = kubernetes_tools.KubernetesDeployStatus.tostring(deploy_status)
    kstatus['running_instance_count'] = app.status.ready_replicas if app.status.ready_replicas else 0


def marathon_job_status(
    service,
    instance,
    job_config,
    marathon_apps_with_clients,
    verbose: bool,
) -> MutableMapping[str, Any]:
    job_status_fields = {'app_statuses': []}
    
    # TODO: probably just merge this with marathon_instance_status
    try:
        desired_app_id = job_config.format_marathon_app_dict()['id']
    except NoDockerImageError:
        error_msg = "Docker image is not in deployments.json."
        job_status_fields['error_message'] = error_msg
        return job_status_fields

    job_status_fields['desired_app_id'] = desired_app_id

    deploy_status_for_desired_app = None
    dashboard_links = get_marathon_dashboard_links(settings.marathon_clients, settings.system_paasta_config)
    tasks_running = 0
    for app, marathon_client in marathon_apps_with_clients:
        deploy_status = marathon_tools.get_marathon_app_deploy_status(marathon_client, app)

        app_status = marathon_app_status(
            app,
            marathon_client,
            dashboard_links,
            deploy_status,
            list_tasks=verbose
        )
        job_status_fields['app_statuses'].append(app_status)

        if app.id.lstrip('/') == desired_app_id.lstrip('/'):
            deploy_status_for_desired_app = marathon_tools.MarathonDeployStatus.tostring(deploy_status)
        tasks_running += app.tasks_running

    job_status_fields['deploy_status'] = deploy_status_for_desired_app or 'Waiting for bounce'
    job_status_fields['running_instance_count'] = tasks_running

    if verbose is True:
        # TODO: this was here originally -- why?  I am inclined to remove it
        job_status_fields['slaves'] = list(
            {a_sync.block(task.slave)['hostname'] for task in a_sync.block(get_running_tasks_from_frameworks, desired_app_id)},
        )
        autoscaling_info = get_autoscaling_info(marathon_apps_with_clients, job_config)
        job_status_fields['autoscaling_info'] = autoscaling_info._asdict()

    return job_status_fields


# TODO: types
def marathon_app_status(
    app,
    marathon_client,
    dashboard_links,
    deploy_status,
    list_tasks: bool = False,
) -> MutableMapping[str, Any]:
    app_status = {
        'tasks_running': app.tasks_running,
        'tasks_healthy': app.tasks_healthy,
        'tasks_staged': app.tasks_staged,
        'tasks_total': app.instances,
        'create_datetime': app.version,  # TODO: get a unixtimestamp instead?
        'deploy_status': marathon_tools.MarathonDeployStatus.tostring(deploy_status),
    }

    app_queue = marathon_tools.get_app_queue(marathon_client, app.id)
    if deploy_status == marathon_tools.MarathonDeployStatus.Delayed:
        _, backoff_seconds = marathon_tools.get_app_queue_status_from_queue(app_queue)
        app_status['backoff_seconds'] = backoff_seconds

    # TODO: unsure what to do with this?  maybe make it a collection
    # TODO: see use in marathon_serviceinit
    unused_offers_summary = marathon_tools.summarize_unused_offers(app_queue)

    dashboard_base_url = dashboard_links.get(marathon_client)
    if dashboard_base_url:
        app_status['dashboard_url'] = "{}/ui/#/apps/%2F{}".format(
            dashboard_base_url.rstrip('/'),
            app.id.lstrip('/'),
        )

    if list_tasks is True:
        app_status['tasks'] = []
        for task in app.tasks:
            app_status['tasks'].append(build_marathon_task_dict(task))

    return app_status


def build_marathon_task_dict(marathon_task):
    if marathon_task.health_check_results:
        is_healthy = marathon_tools.is_task_healthy(marathon_task)
    else:
        is_healthy = None

    return {
        'id': get_short_task_id(marathon_task.id),
        'host': marathon_task.host.split('.')[0],
        'port': marathon_task.ports[0],
        'deployed_time': marathon_task.staged_at.timestamp(),
        'is_healthy': is_healthy,
    }


def marathon_smartstack_status(
    job_config,
    service_namespace_config,
    expected_smartstack_count,
    tasks,
    should_return_individual_backends=False,
) -> Mapping[str, Any]:
    registration = job_config.get_registrations()[0]
    discover_location_type = service_namespace_config.get_discover()
    monitoring_blacklist = job_config.get_monitoring_blacklist(
        system_deploy_blacklist=settings.system_paasta_config.get_deploy_blacklist(),
    )
    filtered_slaves = get_all_slaves_for_blacklist_whitelist(
        blacklist=monitoring_blacklist,
        whitelist=[],
    )
    grouped_slaves = get_mesos_slaves_grouped_by_attribute(
        slaves=filtered_slaves,
        attribute=discover_location_type,
    )

    # rebuild the dict, replacing the slave object with just their hostname
    slave_hostname_by_location = {
        attribute_value: [slave['hostname'] for slave in slaves]
        for attribute_value, slaves in grouped_slaves.items()
    }

    expected_count_per_location = int(expected_smartstack_count / len(slave_hostname_by_location))
    smartstack_status = {
        'registration': registration,
        'expected_backends_per_location': expected_count_per_location,
        'locations': [],
    }

    for location, hosts in slave_hostname_by_location.items():
        synapse_host = hosts[0]
        sorted_backends = sorted(
            get_backends(
                registration,
                synapse_host=synapse_host,
                synapse_port=settings.system_paasta_config.get_synapse_port(),
                synapse_haproxy_url_format=settings.system_paasta_config.get_synapse_haproxy_url_format(),
            ),
            key=lambda backend: backend['status'],
            reverse=True,  # put 'UP' backends above 'MAINT' backends
        )
        matched_backends_and_tasks = match_backends_and_tasks(sorted_backends, tasks)
        location_dict = build_smartstack_location_dict(
            location,
            matched_backends_and_tasks,
            should_return_individual_backends,
        )
        smartstack_status['locations'].append(location_dict)

    return smartstack_status


def build_smartstack_location_dict(
    location,
    matched_backends_and_tasks,
    should_return_individual_backends=False
):
    running_backends_count = 0
    backends = []
    for backend, task in matched_backends_and_tasks:
        if backend is None:
            continue
        if backend_is_up(backend):
            running_backends_count += 1
        if should_return_individual_backends:
            backends.append(build_smartstack_backend_dict(backend, task))

    return {
        'name': location,
        'running_backends_count': running_backends_count,
        'backends': backends,
    }


def build_smartstack_backend_dict(smartstack_backend, task):
    svname = smartstack_backend['svname']
    hostname = svname.split("_")[0]
    port = svname.split("_")[-1].split(":")[-1]
    return {
        'hostname': hostname,
        'port': int(port),
        'status': smartstack_backend['status'],
        'check_status': smartstack_backend['check_status'],
        'check_code': smartstack_backend['check_code'],
        'check_duration': int(smartstack_backend['check_duration']),
        'last_change': int(smartstack_backend['lastchg']),
        'has_associated_task': task is not None,
    }
    

def marathon_instance_status(
    instance_status: Mapping[str, Any],
    service: str,
    instance: str,
    verbose: bool,
) -> Mapping[str, Any]:
    mstatus: Dict[str, Any] = {}
    job_config = marathon_tools.load_marathon_service_config(
        service, instance, settings.cluster, soa_dir=settings.soa_dir,
    )
    current_client = settings.marathon_clients.get_current_client_for_service(job_config)

    # bouncing status can be inferred from app_count, ref get_bouncing_status
    # status_desired_state
    apps = marathon_tools.get_matching_appids(service, instance, current_client)
    mstatus['app_count'] = len(apps)
    mstatus['desired_state'] = job_config.get_desired_state()
    mstatus['bounce_method'] = job_config.get_bounce_method()
    mstatus['expected_instance_count'] = job_config.get_instances()  # NOTE: this is normal_instance_count

    marathon_apps_with_clients = marathon_tools.get_marathon_apps_with_clients(
        clients=settings.marathon_clients.get_all_clients_for_service(job_config),
        embed_tasks=True,
        service_name=service
    )
    matching_apps_with_clients = marathon_tools.get_matching_apps_with_clients(
        service,
        instance,
        marathon_apps_with_clients,
    )
    mstatus['app_count'] = len(marathon_apps_with_clients)

    mstatus.update(
        marathon_job_status(  # TODO: call with kwargs
            service,
            instance,
            job_config,
            marathon_apps_with_clients,
            verbose, 
        )
    )

    mstatus['mesos'] = marathon_mesos_status(service, instance, job_config.get_instances(), verbose)

    service_namespace_config = marathon_tools.load_service_namespace_config(
        service=service,
        namespace=job_config.get_nerve_namespace(),
        soa_dir=settings.soa_dir,
    )
    if 'proxy_port' in service_namespace_config:
        tasks = [
            task for app, _ in marathon_apps_with_clients
            for task in app.tasks
        ]
        
        # TODO: move this into `marathon_smartstack_status` ?
        normal_smartstack_count = marathon_tools.get_expected_instance_count_for_namespace(
            service,
            instance,
            settings.cluster,
        )
        mstatus['smartstack'] = marathon_smartstack_status(  # TODO: call with kwargs
            job_config,
            service_namespace_config,
            normal_smartstack_count,
            tasks,
            should_return_individual_backends=verbose,
        )

    return mstatus


@a_sync.to_blocking
async def marathon_mesos_status(service, instance, expected_instance_count, verbose):
    mesos_status = {}

    job_id = marathon_tools.format_job_id(service, instance)
    job_id_filter_string = f'{job_id}{marathon_tools.MESOS_TASK_SPACER}'
    running_and_active_tasks = select_tasks_by_id(  # TODO: error handling on ReadTimeout?
        await get_cached_list_of_running_tasks_from_frameworks(),
        job_id=job_id_filter_string,
    )

    mesos_status['task_count'] = len(running_and_active_tasks)

    if verbose:
        running_task_dict_futures = []
        for task in running_and_active_tasks:
            running_task_dict_futures.append(
                asyncio.ensure_future(get_mesos_running_task_dict(task))
            )

        non_running_tasks = select_tasks_by_id(
            await get_cached_list_of_not_running_tasks_from_frameworks(),
            job_id=job_id_filter_string,
        )
        non_running_task_dict_futures = []
        for task in non_running_tasks:
            non_running_task_dict_futures.append(
                asyncio.ensure_future(get_mesos_non_running_task_dict(task))
            )

        if len(running_task_dict_futures):
            await asyncio.wait(running_task_dict_futures)
        if len(non_running_task_dict_futures):
            await asyncio.wait(non_running_task_dict_futures)

        mesos_status['running_tasks'] = [
            task_future.result() for task_future in running_task_dict_futures
        ]
        mesos_status['non_running_tasks'] = [
            task_future.result() for task_future in non_running_task_dict_futures
        ]

    return mesos_status


# TODO: how to do this with error handling
# TODO: get comments about calculating cpu usage either from here or elsewhere
async def get_mesos_running_task_dict(task):
    short_hostname_future = asyncio.ensure_future(get_short_hostname_from_task(task))
    mem_limit_future = asyncio.ensure_future(task.mem_limit())
    rss_future = asyncio.ensure_future(task.rss())
    cpu_shares_future = asyncio.ensure_future(task.cpu_limit())
    task_stats_future = asyncio.ensure_future(task.stats())

    await asyncio.wait([short_hostname_future, mem_limit_future, rss_future, cpu_shares_future, task_stats_future])

    task_stats = task_stats_future.result()
    cpu_used_seconds = task_stats.get('cpus_system_time_secs', 0.0) + task_stats.get('cpus_user_time_secs', 0.0)

    task_start_time = float(task['statuses'][0]['timestamp'])
    current_time = int(datetime.datetime.now().strftime('%s'))
    duration_seconds = current_time - round(task_start_time)

    return {
        'id': get_short_task_id(task['id']),
        'hostname': short_hostname_future.result(),
        'mem_limit': mem_limit_future.result(),
        'rss': rss_future.result(),
        'cpu_shares': cpu_shares_future.result() - 0.1,  # TODO: comment
        'cpu_used_seconds': cpu_used_seconds,
        'duration_seconds': duration_seconds,
        'deployed_at': task_start_time,
    }


async def get_mesos_non_running_task_dict(task):
    return {
        'id': get_short_task_id(task['id']),
        'hostname': await get_short_hostname_from_task(task),
        'deployed_at': task['statuses'][0]['timestamp'],
        'state': task['state'],
    }


@view_config(route_name='service.instance.status', request_method='GET', renderer='json')
def instance_status(request):
    service = request.swagger_data.get('service')
    instance = request.swagger_data.get('instance')
    verbose = request.swagger_data.get('verbose', False)

    instance_status: Dict[str, Any] = {}
    instance_status['service'] = service
    instance_status['instance'] = instance

    try:
        instance_type = validate_service_instance(service, instance, settings.cluster, settings.soa_dir)
    except NoConfigurationForServiceError:
        error_message = 'deployment key %s not found' % '.'.join([settings.cluster, instance])
        raise ApiFailure(error_message, 404)
    except Exception:
        error_message = traceback.format_exc()
        raise ApiFailure(error_message, 500)

    print(instance_type)
    if instance_type != 'flinkcluster' and instance_type != 'tron':
        try:
            actual_deployments = get_actual_deployments(service, settings.soa_dir)
        except Exception:
            error_message = traceback.format_exc()
            raise ApiFailure(error_message, 500)

        version = get_deployment_version(actual_deployments, settings.cluster, instance)
        # exit if the deployment key is not found
        if not version:
            error_message = 'deployment key %s not found' % '.'.join([settings.cluster, instance])
            raise ApiFailure(error_message, 404)

        instance_status['git_sha'] = version
    else:
        instance_status['git_sha'] = ''

    try:
        if instance_type == 'marathon':
            instance_status['marathon'] = marathon_instance_status(instance_status, service, instance, verbose)
        elif instance_type == 'chronos':
            instance_status['chronos'] = chronos_instance_status(instance_status, service, instance, verbose)
        elif instance_type == 'adhoc':
            instance_status['adhoc'] = adhoc_instance_status(instance_status, service, instance, verbose)
        elif instance_type == 'kubernetes':
            instance_status['kubernetes'] = kubernetes_instance_status(instance_status, service, instance, verbose)
        elif instance_type == 'tron':
            instance_status['tron'] = tron_instance_status(instance_status, service, instance, verbose)
        elif instance_type == 'flinkcluster':
            status = flinkcluster_instance_status(instance_status, service, instance, verbose)
            if status is not None:
                instance_status['flinkcluster'] = {'status': status}
            else:
                instance_status['flinkcluster'] = {}
        else:
            error_message = f'Unknown instance_type {instance_type} of {service}.{instance}'
            raise ApiFailure(error_message, 404)
    except Exception:
        error_message = traceback.format_exc()
        raise ApiFailure(error_message, 500)

    return instance_status


@view_config(route_name='service.instance.tasks.task', request_method='GET', renderer='json')
def instance_task(request):
    status = instance_status(request)
    task_id = request.swagger_data.get('task_id', None)
    verbose = request.swagger_data.get('verbose', False)
    try:
        mstatus = status['marathon']
    except KeyError:
        raise ApiFailure("Only marathon tasks supported", 400)
    try:
        task = a_sync.block(get_task, task_id, app_id=mstatus['app_id'])
    except TaskNotFound:
        raise ApiFailure(f"Task with id {task_id} not found", 404)
    except Exception:
        error_message = traceback.format_exc()
        raise ApiFailure(error_message, 500)
    if verbose:
        task = add_slave_info(task)
        task = add_executor_info(task)
    return task._Task__items


@view_config(route_name='service.instance.tasks', request_method='GET', renderer='json')
def instance_tasks(request):
    status = instance_status(request)
    slave_hostname = request.swagger_data.get('slave_hostname', None)
    verbose = request.swagger_data.get('verbose', False)
    try:
        mstatus = status['marathon']
    except KeyError:
        raise ApiFailure("Only marathon tasks supported", 400)
    tasks = a_sync.block(get_tasks_from_app_id, mstatus['app_id'], slave_hostname=slave_hostname)
    if verbose:
        tasks = [add_executor_info(task) for task in tasks]
        tasks = [add_slave_info(task) for task in tasks]
    return [task._Task__items for task in tasks]


@view_config(route_name="service.instance.delay", request_method='GET', renderer='json')
def instance_delay(request):
    service = request.swagger_data.get('service')
    instance = request.swagger_data.get('instance')
    job_config = marathon_tools.load_marathon_service_config(
        service, instance, settings.cluster, soa_dir=settings.soa_dir,
    )
    client = settings.marathon_clients.get_current_client_for_service(job_config)
    app_id = job_config.format_marathon_app_dict()['id']
    app_queue = marathon_tools.get_app_queue(client, app_id)
    unused_offers_summary = marathon_tools.summarize_unused_offers(app_queue)

    if len(unused_offers_summary) != 0:
        return unused_offers_summary
    else:
        response = Response()
        response.status_int = 204
        return response


def add_executor_info(task):
    task._Task__items['executor'] = a_sync.block(task.executor).copy()
    task._Task__items['executor'].pop('tasks', None)
    task._Task__items['executor'].pop('completed_tasks', None)
    task._Task__items['executor'].pop('queued_tasks', None)
    return task


def add_slave_info(task):
    task._Task__items['slave'] = a_sync.block(task.slave)._MesosSlave__items.copy()
    return task
