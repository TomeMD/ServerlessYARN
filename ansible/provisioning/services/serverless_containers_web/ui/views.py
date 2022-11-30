from django.shortcuts import render,redirect
from ui.forms import RuleForm, DBSnapshoterForm, GuardianForm, ScalerForm, StructuresSnapshoterForm, SanityCheckerForm, RefeederForm, ReBalancerForm, LimitsForm, StructureResourcesForm, StructureResourcesFormSetHelper, HostResourcesForm, HostResourcesFormSetHelper, RemoveStructureForm, AddHostForm, AddContainerForm, AddNContainersFormSetHelper, AddNContainersForm
from django.forms import formset_factory
from django.http import HttpResponse
#from ui.update_inventory_file import add_containers_to_hosts,remove_container_from_host, add_host, remove_host
import urllib.request
import urllib.parse
import json
import requests
import time
import yaml
from bs4 import BeautifulSoup
#import subprocess

from ui.background_tasks import start_containers_task, add_host_task, remove_container_task, remove_host_task, remove_app_task
from celery.result import AsyncResult


config_path = "../../config/config.yml"
with open(config_path, "r") as config_file:
    config = yaml.load(config_file, Loader=yaml.FullLoader)

base_url = "http://{0}:{1}".format(config['server_ip'],config['orchestrator_port'])
pendings_taks = []

## Auxiliary general methods
def redirect_with_errors(redirect_url, errors):

    red = redirect(redirect_url)
    if (len(errors) > 0):
        red['Location'] += '?errors=' + urllib.parse.quote(errors[0])

        i = 1
        while(i < len(errors)):
            red['Location'] += '&errors=' + urllib.parse.quote(errors[i])
            i += 1

    else:
        red['Location'] += '?success=' + "Requests were successful!"

    return red

def register_task(task_id, task_name):
    global pendings_taks
    pendings_taks.append((task_id,task_name))

def get_pending_tasks():
    global pendings_taks
    still_pending_tasks = []
    successful_tasks = []
    failed_tasks = []

    for task_id, task_name in pendings_taks:
        task_result = AsyncResult(task_id)
        status = task_result.status

        if status != "SUCCESS" and status != "FAILURE":
            still_pending_tasks.append((task_id,task_name))
        elif status == "SUCCESS":
            successful_tasks.append((task_id,task_name))
        else:
            failed_tasks.append((task_id,task_name,task_result.result))

    for task_id, task_name in successful_tasks:
        pendings_taks.remove((task_id, task_name))

    for task_id, task_name, task_error in failed_tasks:
        pendings_taks.remove((task_id, task_name))

    # TODO: remove pending tasks after a timeout

    return still_pending_tasks, successful_tasks, failed_tasks

def get_pendings_tasks_to_string():
    still_pending_tasks, successful_tasks, failed_tasks = get_pending_tasks()

    still_pending_tasks_string = []
    successful_tasks_string = []
    failed_tasks_string = []

    for task_id, task_name in still_pending_tasks:
        info = "Task with ID {0} and name {1} is pending".format(task_id,task_name)
        still_pending_tasks_string.append(info)

    for task_id, task_name in successful_tasks:
        success = "Task with ID {0} and name {1} has completed successfully".format(task_id,task_name)
        successful_tasks_string.append(success)

    for task_id, task_name, task_error in failed_tasks:
        error = "Task with ID {0} and name {1} has failed with error: {2}".format(task_id,task_name,task_error)
        failed_tasks_string.append(error)

    return still_pending_tasks_string, successful_tasks_string, failed_tasks_string

## Home
def index(request):
    url = base_url + "/heartbeat"
    
    try:
        response = urllib.request.urlopen(url)
        data_json = json.loads(response.read())
    except urllib.error.URLError:
        data_json = {"status": "down"}

    return render(request, 'index.html', {'data': data_json})
    

#### Structures
# Views
def structure_detail(request,structure_name):
    url = base_url + "/structure/" + structure_name

    response = urllib.request.urlopen(url)
    data_json = json.loads(response.read())
    return HttpResponse(json.dumps(data_json), content_type="application/json")

def structures(request, structure_type, html_render):
    url = base_url + "/structure/"

    if (len(request.POST) > 0):
        errors = []

        resources_errors = processResources(request, url)
        limits_errors = processLimits(request, url)
        add_errors = processAdds(request, url)
        removes_errors = processRemoves(request, url, structure_type)

        if (resources_errors): errors += resources_errors
        if (limits_errors): errors += limits_errors
        if (add_errors): errors += add_errors
        if (removes_errors): errors += removes_errors

        return redirect_with_errors(structure_type,errors)

    requests_errors = request.GET.getlist("errors", None)
    requests_successes = request.GET.getlist("success", None)
    requests_info = []

    ## Pending tasks
    still_pending_tasks, successful_tasks, failed_tasks = get_pendings_tasks_to_string()
    requests_errors.extend(failed_tasks)
    requests_successes.extend(successful_tasks)
    requests_info.extend(still_pending_tasks)

    try:
        response = urllib.request.urlopen(url)
        data_json = json.loads(response.read())
    except urllib.error.HTTPError:
        data_json = {}
    
    structures = []
    addStructureForm = None
    if (structure_type == "containers"):
        structures = getContainers(data_json)
        hosts = getHostsNames(data_json)
        addStructureForm = {}
        addStructureForm['add_container'] = AddContainerForm()
        addStructureForm['add_n_containers'] = setAddNContainersForm(structures,hosts,structure_type)

    elif(structure_type == "hosts"):
        structures = getHosts(data_json)
        addStructureForm = AddHostForm()

    elif(structure_type == "apps"):
        structures = getApps(data_json)

    ## Set RemoveStructures Form
    removeStructuresForm = setRemoveStructureForm(structures,structure_type)

    return render(request, html_render, {'data': structures, 'requests_errors': requests_errors, 'requests_successes': requests_successes, 'requests_info': requests_info, 'addStructureForm': addStructureForm,'removeStructuresForm': removeStructuresForm})


def containers(request):
    return structures(request, "containers","containers.html")
    
def hosts(request):
    return structures(request, "hosts","hosts.html")

def apps(request):
    return structures(request, "apps","apps.html")

# Prepare data to HTML
def getHosts(data):
    hosts = []

    for item in data:
        if (item['subtype'] == 'host'):
            containers = []
            for structure in data:
                if (structure['subtype'] == 'container' and structure['host'] == item['name']):
                    structure['limits'] = getLimits(structure['name'])

                    ## Container Resources Form
                    setStructureResourcesForm(structure,"hosts")

                    ## Container Limits Form
                    setLimitsForm(structure,"hosts")

                    ## Set labels for container values
                    structure['resources_values_labels'] = getStructuresValuesLabels(structure, 'resources')
                    structure['limits_values_labels'] = getStructuresValuesLabels(structure, 'limits') 

                    containers.append(structure)

            ## we order this list using name container to keep the order consistent with the 'cpu_cores' dict below
            item['containers'] = sorted(containers, key=lambda d: d['name']) 

            # Adjustment to don't let core_usage_mapping be too wide on html display
            if ("cpu" in item['resources'] and "core_usage_mapping" in item['resources']['cpu']):
                core_mapping = item['resources']['cpu']['core_usage_mapping']
                core_mapping = {int(k) : v for k, v in core_mapping.items()}
                core_mapping = dict(sorted(core_mapping.items()))

                ## fill usage with 0 when a container doesn't show in a core
                for core,mapping in list(core_mapping.items()):
                    for cont in item['containers']:
                        if (cont['name'] not in mapping):
                            mapping[cont['name']] = 0

                    mapping = dict(sorted(mapping.items()))

                    ## Move 'free' shares of core always to start of dict
                    free = mapping['free']
                    mapping_list = list(mapping.items())
                    mapping_list.remove(('free',free))
                    mapping_list.insert(0,('free',free))

                    core_mapping[core] = dict(mapping_list)

                item['resources']['cpu_cores'] = core_mapping

                # remove core_usage_mapping from the cpu resources since now that info is in 'cpu_cores'
                item['resources']['cpu'] = {k:v for k,v in item['resources']['cpu'].items() if k != 'core_usage_mapping'}

            ## Host Resources Form
            setStructureResourcesForm(item,"hosts")

            ## Set labels for host values
            item['resources_values_labels'] = getStructuresValuesLabels(item, 'resources')

            hosts.append(item)
                          
    return hosts

def getHostsNames(data):

    hosts = []

    for item in data:
        if (item['subtype'] == 'host'):

            hosts.append(item)

    return hosts

def getApps(data):
    apps = []

    for item in data:
        if (item['subtype'] == 'application'):
            containers = []
            for structure in data:
                if (structure['subtype'] == 'container' and structure['name'] in item['containers']):
                    structure['limits'] = getLimits(structure['name'])

                    ## Container Resources Form
                    setStructureResourcesForm(structure,"apps")

                    ## Container Limits Form
                    setLimitsForm(structure,"apps")

                    ## Set labels for container values
                    structure['resources_values_labels'] = getStructuresValuesLabels(structure, 'resources')
                    structure['limits_values_labels'] = getStructuresValuesLabels(structure, 'limits')

                    containers.append(structure)

            item['containers_full'] = containers
            item['limits'] = getLimits(item['name'])

            ## App Resources Form
            setStructureResourcesForm(item,"apps")

            ## App Limits Form
            setLimitsForm(item,"apps")

            ## Set labels for apps values
            item['resources_values_labels'] = getStructuresValuesLabels(item, 'resources')
            item['limits_values_labels'] = getStructuresValuesLabels(item, 'limits')

            apps.append(item)
    return apps

def getContainers(data):
    containers = []

    for item in data:
        if (item['subtype'] == 'container'):
            item['limits'] = getLimits(item['name'])

            ## Container Resources Form
            setStructureResourcesForm(item,"containers")

            ## Container Limits Form
            setLimitsForm(item,"containers")

            ## Set labels for container values
            item['resources_values_labels'] = getStructuresValuesLabels(item, 'resources')
            item['limits_values_labels'] = getStructuresValuesLabels(item, 'limits')

            containers.append(item)
    return containers

def getStructuresValuesLabels(item, field):

    values_labels = []

    if (field in item and len(list(item[field].keys())) > 0):
        first_key = list(item[field].keys())[0]

        if (first_key != 'cpu_cores'):
            values_labels = item[field][first_key]
        else:
            ## just in case we get 'cpu_cores' field from a host as first key
            values_labels = item[field]['cpu']

    return values_labels


def setStructureResourcesForm(structure, form_action):

    editable_data = 0

    full_resource_list = ["cpu","mem","disk","net","energy"]
    structures_resources_field_list = ["guard","max","min"]
    host_resources_field_list = ["max"]
    form_initial_data_list = []

    resource_list = []
    for resource in full_resource_list:
        if (resource in structure['resources']):
            resource_list.append(resource)

    if (structure['subtype'] == "host"):
        resources_field_list = host_resources_field_list
    else:
        resources_field_list = structures_resources_field_list

    for resource in resource_list:
        form_initial_data = {'name' : structure['name'], 'structure_type' : structure['subtype'], 'resource' : resource}

        for field in resources_field_list:
            if (field in structure["resources"][resource]):
                editable_data += 1
                form_initial_data[field] = structure["resources"][resource][field]            

        form_initial_data_list.append(form_initial_data)

    if (structure['subtype'] == "host"):
        HostResourcesFormSet = formset_factory(HostResourcesForm, extra = 0)
        structure['resources_form'] = HostResourcesFormSet(initial = form_initial_data_list)
        structure['resources_form_helper'] = HostResourcesFormSetHelper()
        submit_button_disp = 5
    else:
        StructureResourcesFormSet = formset_factory(StructureResourcesForm, extra = 0)
        structure['resources_form'] = StructureResourcesFormSet(initial = form_initial_data_list)
        structure['resources_form_helper'] = StructureResourcesFormSetHelper()
        submit_button_disp = 7

    structure['resources_form_helper'].form_action = form_action
    structure['resources_editable_data'] = editable_data

    ## Need to do this to hide extra 'Save changes' buttons on JS
    structure['resources_form_helper'].layout[submit_button_disp][0].name += structure['name']

def getLimits(structure_name):
    url = base_url + "/structure/" + structure_name + "/limits"
    
    try:
        response = urllib.request.urlopen(url)
        data_json = json.loads(response.read())
    except urllib.error.HTTPError:
        data_json = {}
    
    
    return data_json
  
def setLimitsForm(structure, form_action):

    editable_data = 0
    form_initial_data = {'name' : structure['name']}

    resource_list = ["cpu","mem","disk","net","energy"]

    for resource in resource_list:
        if (resource in structure['limits'] and 'boundary' in structure['limits'][resource]): 
            editable_data += 1
            form_initial_data[resource + '_boundary'] = structure['limits'][resource]['boundary']
    
    structure['limits_form'] = LimitsForm(initial = form_initial_data)
    structure['limits_form'].helper.form_action = form_action
    structure['limits_editable_data'] = editable_data
    
    for resource in resource_list:
        if ( not (resource in structure['limits'] and 'boundary' in structure['limits'][resource])):    
            structure['limits_form'].helper[resource + '_boundary'].update_attributes(type="hidden")

def setAddNContainersForm(structures, hosts, form_action):

    form_initial_data_list = []
    editable_data = len(hosts)

    for host in hosts:
        form_initial_data = {'operation' : "add", 'structure_type' : "Ncontainers", 'host' : host['name'], 'containers_added': 0}
        form_initial_data_list.append(form_initial_data)

    formSet = formset_factory(AddNContainersForm, extra = 0)

    addNform = {}

    addNform['form'] = formSet(initial = form_initial_data_list)
    addNform['helper'] = AddNContainersFormSetHelper()
    addNform['helper'].form_action = form_action
    addNform['editable_data'] = editable_data

    #submit_button_disp = 4
    ## Need to do this to hide extra 'Save changes' buttons on JS
    #addNform['helper'].layout[submit_button_disp][0].name += structure['name']

    return addNform

def setRemoveStructureForm(structures, form_action):

    removeStructuresForm = RemoveStructureForm()
    removeStructuresForm.helper.form_action = form_action

    for structure in structures:
        if (form_action == "containers"):
            removeStructuresForm.fields['structures_removed'].choices.append(((structure['name'],structure['host']),structure['name']))
        else:
            removeStructuresForm.fields['structures_removed'].choices.append((structure['name'],structure['name']))

    return removeStructuresForm

# Process POST requests
def containers_guard_switch(request, container_name):

    guard_switch(request, container_name)
    return redirect("containers")

def hosts_guard_switch(request, container_name):

    guard_switch(request, container_name)
    return redirect("hosts")

def apps_guard_switch(request, structure_name):

    # we may be switching a container or an app

    guard_switch(request, structure_name)
    return redirect("apps")

def guard_switch(request, structure_name):

    state = request.POST['guard_switch']

    ## send put to stateDatabase
    url = base_url + "/structure/" + structure_name 

    if (state == "guard_off"):
        url += "/unguard"
    else:
        url += "/guard"

    req = urllib.request.Request(url, method='PUT')

    try:
        response = urllib.request.urlopen(req)
    except:
        pass

def processResources(request, url):

    structures_resources_field_list = ["guard","max","min"]
    hosts_resources_field_list = ["max"]

    errors = []

    if ("form-TOTAL_FORMS" in request.POST):
        total_forms = int(request.POST['form-TOTAL_FORMS'])

        if (total_forms > 0 and request.POST['form-0-operation'] == "resources"):
            
            name = request.POST['form-0-name']

            if (request.POST['form-0-structure_type'] == "host"):
                resources_field_list = hosts_resources_field_list
            else:
                resources_field_list = structures_resources_field_list     

            for i in range(0,total_forms,1):
                resource = request.POST['form-' + str(i) + "-resource"]
    
                for field in resources_field_list:
                    field_value = request.POST['form-' + str(i) + "-" + field]
                    error = processResourcesFields(request, url, name, resource, field, field_value)
                    if (len(error) > 0): errors.append(error)

    return errors

def processResourcesFields(request, url, structure_name, resource, field, field_value):

    full_url = url + structure_name + "/resources/" + resource + "/"

    if (field == "guard"):
        if (field_value == "True"): full_url += "guard"
        else:                       full_url += "unguard"
    
        r = requests.put(full_url)

    else:
        full_url += field
        headers = {'Content-Type': 'application/json'}

        new_value = field_value
        put_field_data = {'value': new_value.lower()}

        r = requests.put(full_url, data=json.dumps(put_field_data), headers=headers)

    error = ""
    if (r.status_code != requests.codes.ok):
        soup = BeautifulSoup(r.text, features="html.parser")
        error = "Error submiting " + field + " for structure " + structure_name + ": " + soup.get_text().strip()

    return error

def processLimits(request, url):

    errors = []

    if ("name" in request.POST and "operation" not in request.POST):
        
        structure_name = request.POST['name']

        resources = ["cpu","mem","disk","net","energy"]

        for resource in resources:
            if (resource + "_boundary" in request.POST):
                error = processLimitsBoundary(request, url, structure_name, resource, resource + "_boundary")
                if (len(error) > 0): errors.append(error)

    return errors

def processLimitsBoundary(request, url, structure_name, resource, boundary_name):

    full_url = url + structure_name + "/limits/" + resource + "/boundary"
    headers = {'Content-Type': 'application/json'}

    new_value = request.POST[boundary_name]

    r = ""
    if (new_value != ''):
        put_field_data = {'value': new_value.lower()}

        r = requests.put(full_url, data=json.dumps(put_field_data), headers=headers)

    error = ""
    if (r != "" and r.status_code != requests.codes.ok):
        soup = BeautifulSoup(r.text, features="html.parser")
        error = "Error submiting " + boundary_name + " for structure " + structure_name + ": " + soup.get_text().strip()

    return error

def processAdds(request, url):
    errors = []

    if ("operation" in request.POST and request.POST["operation"] == "add"):
        
        structure_name = request.POST['name']
        structure_type = request.POST['structure_type']

        resources = ["cpu","mem","disk","net","energy"]

        error = ""

        if (structure_type == "host"):
            error = processAddHost(request, url, structure_name, structure_type, resources)
        elif (structure_type == "container"):
            host = request.POST['host']
            error = processAddContainer(request, url, structure_name, structure_type, resources, host)

        if (len(error) > 0): errors.append(error)

    elif ("form-TOTAL_FORMS" in request.POST and request.POST['form-0-operation'] == "add"):
        total_forms = int(request.POST['form-TOTAL_FORMS'])

        for i in range(0,total_forms,1):
            host = request.POST['form-' + str(i) + "-host"]
            containers_added = request.POST['form-' + str(i) + "-containers_added"]
            error = processAddNContainers(request, url, host, containers_added)
            if (len(error) > 0): errors.append(error)

    return errors

def processAddHost(request, url, structure_name, structure_type, resources):
    error = ""

    new_containers = int(request.POST['number_of_containers'])
    cpu = request.POST['cpu_max']
    mem = request.POST['mem_max']

    # provision host and start its containers from playbook
    task = add_host_task(structure_name,cpu,mem,new_containers)
    print("Starting task with id {0}".format(task.id))
    register_task(task.id,"add_host_task")

    return error

# Not used ATM
def processAddHost_via_API(request, url, structure_name, structure_type, resources):

    full_url = url + structure_type + "/" + structure_name
    headers = {'Content-Type': 'application/json'}

    put_field_data = {
        'name': structure_name,
        'host': structure_name,
        'subtype': "host",
        'host_rescaler_ip': structure_name,
        'host_rescaler_port': 8000,
        'resources': {}
    }

    for resource in resources:
        if (resource + "_max" in request.POST):
            resource_max = request.POST[resource + "_max"]
            put_field_data['resources'][resource] = {'max': int(resource_max), 'free': int(resource_max)}

    r = requests.put(full_url, data=json.dumps(put_field_data), headers=headers)

    error = ""
    if (r != "" and r.status_code != requests.codes.ok):
        soup = BeautifulSoup(r.text, features="html.parser")
        error = "Error adding host " + structure_name + ": " + soup.get_text().strip()

    return error

# Does not start added containers ATM
def processAddContainer(request, url, structure_name, structure_type, resources, host):

    full_url = url + structure_type + "/" + structure_name
    headers = {'Content-Type': 'application/json'}

    put_field_data = {
        'container': {
            'name': structure_name,
            'resources': {},
            'host_rescaler_ip': host,
            'host_rescaler_port': 8000,
            'host': host,
            'guard': 'false',
            'subtype': "container",
        },
        'limits': {'resources': {}}
    }

    for resource in resources:
        if (resource + "_max" in request.POST):
            resource_max = request.POST[resource + "_max"]
            resource_min = request.POST[resource + "_min"]
            put_field_data['container']['resources'][resource] = {'max': int(resource_max), 'current': int(resource_max), 'min': int(resource_min), 'guard': 'false'}

        if (resource + "_boundary" in request.POST):
            resource_boundary = request.POST[resource + "_boundary"]
            put_field_data['limits']['resources'][resource] = {'boundary': int(resource_boundary)}

    #rc = subprocess.Popen(["./ui/scripts/start_container.sh", host, structure_name])
    #rc.communicate()

    r = requests.put(full_url, data=json.dumps(put_field_data), headers=headers)

    error = ""
    if (r != "" and r.status_code != requests.codes.ok):
        soup = BeautifulSoup(r.text, features="html.parser")
        error = "Error adding container " + structure_name + ": " + soup.get_text().strip()

    return error

def processAddNContainers(request, url, host, containers_added):
    error = ""

    new_containers = {host: int(containers_added)}

    # start containers from playbook
    task = start_containers_task.delay(host, new_containers)
    print("Starting task with id {0}".format(task.id))
    register_task(task.id,"start_containers_task")

    return error

def processRemoves(request, url, structure_type):

    errors = []

    if ("operation" in request.POST and request.POST["operation"] == "remove"):
        
        structures_to_remove = request.POST.getlist('structures_removed', None)

        for structure_name in structures_to_remove:
            error = processRemoveStructure(request, url, structure_name, structure_type)
            if (len(error) > 0): errors.append(error)

    return errors    
   
def processRemoveStructure(request, url, structure_name, structure_type):

    structure_type_url = ""
    headers = {'Content-Type': 'application/json'}

    if (structure_type == "containers"):
        structure_type_url = "container"
        cont_host = structure_name.strip("(").strip(")").split(',')
        structure_name = cont_host[0].strip().strip("'")
        host_name = cont_host[1].strip().strip("'")

        full_url = url + structure_type_url + "/" + structure_name

        task = remove_container_task.delay(full_url, headers, host_name, structure_name)
        print("Starting task with id {0}".format(task.id))
        register_task(task.id,"remove_container_task")

    elif (structure_type == "hosts"):
        structure_type_url = "host"
        containerList = getContainersFromHost(url, structure_name)

        for container in containerList:
            processRemoveStructure(request, url, "(" + container + "," + structure_name + ")", "containers")

        full_url = url + structure_type_url + "/" + structure_name

        task = remove_host_task.delay(full_url, headers, structure_name)
        print("Starting task with id {0}".format(task.id))
        register_task(task.id,"remove_host_task")

    elif (structure_type == "apps"):
        structure_type_url = "apps"

        full_url = url + structure_type_url + "/" + structure_name

        task = remove_app_task.delay(full_url, headers, structure_name)
        print("Starting task with id {0}".format(task.id))
        register_task(task.id,"remove_app_task")

    error = ""
    return error

## Not used ATM
def processRemoveStructure_sync(request, url, structure_name, structure_type):

    structure_type_url = ""

    if (structure_type == "containers"): 
        structure_type_url = "container"
        cont_host = structure_name.strip("(").strip(")").split(',')
        structure_name = cont_host[0].strip().strip("'")
        host_name = cont_host[1].strip().strip("'")

    elif (structure_type == "hosts"): 
        structure_type_url = "host"
        containerList = getContainersFromHost(url, structure_name)

        for container in containerList:
            processRemoveStructure(request, url, "(" + container + "," + structure_name + ")", "containers")

    elif (structure_type == "apps"): structure_type_url = "apps"

    full_url = url + structure_type_url + "/" + structure_name
    headers = {'Content-Type': 'application/json'}

    r = requests.delete(full_url, headers=headers)
    
    error = ""
    if (r.status_code != requests.codes.ok):
        soup = BeautifulSoup(r.text, features="html.parser")
        error = "Error removing structure " + structure_name + ": " + soup.get_text().strip()

    ## stop container
    if (structure_type == "containers" and error == ""): 

        rc = subprocess.Popen(["./ui/scripts/stop_container.sh", host_name, structure_name])
        rc.communicate()

        # update inventory file
        remove_container_from_host(structure_name,host_name) 

    ## remove host
    elif (structure_type == "hosts" and error == ""):
            
        # stop node scaler service in host
        rc = subprocess.Popen(["./ui/scripts/stop_host_scaler.sh", structure_name])
        rc.communicate()

        # update inventory file            
        remove_host(structure_name)

    return error

def getContainersFromHost(url, host_name):

    try:
        response = urllib.request.urlopen(url)
        data = json.loads(response.read())
    except urllib.error.HTTPError:
        data = {}

    containerList = []

    for item in data:
        if (item['subtype'] == 'container' and item['host'] == host_name):
            containerList.append(item['name'])
                          
    return containerList

## Services
def services(request):
    url = base_url + "/service/"

    database_snapshoter_options = ["debug","documents_persisted","polling_frequency"]
    guardian_options = ["cpu_shares_per_watt", "debug", "event_timeout","guardable_resources","structure_guarded","window_delay","window_timelapse"]
    scaler_options = ["check_core_map","debug","polling_frequency","request_timeout"]
    structures_snapshoter_options = ["polling_frequency","debug","persist_apps","resources_persisted"]
    sanity_checker_options = ["debug","delay"]
    refeeder_options = ["debug","generated_metrics","polling_frequency","window_delay","window_timelapse"]
    rebalancer_options = ["debug","rebalance_users","energy_diff_percentage","energy_stolen_percentage","window_delay","window_timelapse"]

    if (len(request.POST) > 0):
        if ("name" in request.POST):
            errors = []

            service_name = request.POST['name']

            options = []

            if (service_name == 'database_snapshoter'):     options = database_snapshoter_options

            elif (service_name == 'guardian'):              options = guardian_options

            elif (service_name == 'scaler'):                options = scaler_options

            elif (service_name == 'structures_snapshoter'): options = structures_snapshoter_options

            elif (service_name == 'sanity_checker'):        options = sanity_checker_options

            elif (service_name == 'refeeder'):              options = refeeder_options

            elif (service_name == 'rebalancer'):            options = rebalancer_options

            for option in options:
                error = processServiceConfigPost(request, url, service_name, option)
                if (len(error) > 0): errors.append(error)

        return redirect_with_errors('services',errors)

    response = urllib.request.urlopen(url)
    data_json = json.loads(response.read())
        
    requests_errors = request.GET.getlist("errors", None)
    requests_successes = request.GET.getlist("success", None)
    requests_info = []

    ## Pending tasks
    still_pending_tasks, successful_tasks, failed_tasks = get_pendings_tasks_to_string()
    requests_errors.extend(failed_tasks)
    requests_successes.extend(successful_tasks)
    requests_info.extend(still_pending_tasks)

    ## get datetime in epoch to compare later with each service's heartbeat
    now = time.time()

    for item in data_json:

        form_initial_data = {'name' : item['name']}
        editable_data = 0
        
        serviceForm = {}

        if (item['name'] == 'database_snapshoter'):
            for option in database_snapshoter_options:
                if (option.upper() in item['config']): form_initial_data[option] = item['config'][option.upper()]

            editable_data += 1
            serviceForm = DBSnapshoterForm(initial = form_initial_data)

        elif (item['name'] == 'guardian'):
            for option in guardian_options:
                if (option.upper() in item['config']): form_initial_data[option] = item['config'][option.upper()]

            editable_data += 1
            serviceForm = GuardianForm(initial = form_initial_data)

        elif (item['name'] == 'scaler'):
            for option in scaler_options:
                if (option.upper() in item['config']): form_initial_data[option] = item['config'][option.upper()]

            editable_data += 1
            serviceForm = ScalerForm(initial = form_initial_data)

        if (item['name'] == 'structures_snapshoter'):
            for option in structures_snapshoter_options:
                if (option.upper() in item['config']): form_initial_data[option] = item['config'][option.upper()]

            editable_data += 1
            serviceForm = StructuresSnapshoterForm(initial = form_initial_data)

        if (item['name'] == 'sanity_checker'):
            for option in sanity_checker_options:
                if (option.upper() in item['config']): form_initial_data[option] = item['config'][option.upper()]

            editable_data += 1
            serviceForm = SanityCheckerForm(initial = form_initial_data)

        if (item['name'] == 'refeeder'):
            for option in refeeder_options:
                if (option.upper() in item['config']): form_initial_data[option] = item['config'][option.upper()]

            editable_data += 1
            serviceForm = RefeederForm(initial = form_initial_data)

        if (item['name'] == 'rebalancer'):
            for option in rebalancer_options:
                if (option.upper() in item['config']): form_initial_data[option] = item['config'][option.upper()]

            editable_data += 1
            serviceForm = ReBalancerForm(initial = form_initial_data)

        item['form'] = serviceForm
        item['editable_data'] = editable_data

        last_heartbeat = item['heartbeat']
        item['alive'] = (now - last_heartbeat) < 60

    config_errors = checkInvalidConfig()

    return render(request, 'services.html', {'data': data_json, 'config_errors': config_errors, 'requests_errors': requests_errors, 'requests_successes': requests_successes, 'requests_info': requests_info})
  
def service_switch(request,service_name):

    state = request.POST['service_switch']

    ## send put to stateDatabase
    url = base_url + "/service/" + service_name + "/ACTIVE"

    if (state == "service_off"):
        activate = "false"
    else:
        activate = "true"

    headers = {'Content-Type': 'application/json'}

    put_field_data = {'value': activate}
    r = requests.put(url, data=json.dumps(put_field_data), headers=headers)

    if (r.status_code != requests.codes.ok):
        ## shouldn't happen
        print(r.content)

    return redirect('services')

def processServiceConfigPost(request, url, service_name, config_name):

    full_url = url + service_name + "/" + config_name.upper()
    headers = {'Content-Type': 'application/json'}

    json_fields = []
    multiple_choice_fields = ["guardable_resources","resources_persisted","generated_metrics","documents_persisted"]

    r = ""

    if (config_name in json_fields):
        ## JSON field request (not used at the moment)
        new_value = request.POST[config_name]
        new_values_list = new_value.strip("[").strip("]").split(",")
        put_field_data = json.dumps({"value":[v.strip().strip('"') for v in new_values_list]})

        r = requests.put(full_url, data=put_field_data, headers=headers)

    elif (config_name in multiple_choice_fields):
        ## MultipleChoice field request
        new_values_list = request.POST.getlist(config_name)
        put_field_data = json.dumps({"value":[v.strip().strip('"') for v in new_values_list]})

        r = requests.put(full_url, data=put_field_data, headers=headers)

    else:
        ## Other field request
        new_value = request.POST[config_name]
        
        if (new_value != ''):
            put_field_data = {'value': new_value.lower()}
            r = requests.put(full_url, data=json.dumps(put_field_data), headers=headers)

    error = ""
    if (r != "" and r.status_code != requests.codes.ok):
        soup = BeautifulSoup(r.text, features="html.parser")
        error = "Error submiting " + config_name + " for service " + service_name + ": " + soup.get_text().strip()

    return error


## Rules
def rules(request):
    url = base_url + "/rule/"

    if (len(request.POST) > 0):
        errors = []

        rule_name = request.POST['name']
        rules_fields = ["amount","rescale_policy","up_events_required","down_events_required"]
        rules_fields_put_url = ["amount","policy","events_required","events_required"]
        rules_fields_dict = dict(zip(rules_fields, rules_fields_put_url))

        for field in rules_fields:
            if (field in request.POST):
                error = processRulesPost(request, url, rule_name, field, rules_fields_dict[field])
                if (len(error) > 0): errors.append(error)

        return redirect_with_errors('rules',errors)

    response = urllib.request.urlopen(url)
    data_json = json.loads(response.read())
    
    requests_errors = request.GET.getlist("errors", None)
    requests_successes = request.GET.getlist("success", None)
    requests_info = []

    ## Pending tasks
    still_pending_tasks, successful_tasks, failed_tasks = get_pendings_tasks_to_string()
    requests_errors.extend(failed_tasks)
    requests_successes.extend(successful_tasks)
    requests_info.extend(still_pending_tasks)

    for item in data_json:
        item['rule_readable'] = jsonBooleanToHumanReadable(item['rule'])

        form_initial_data = {'name' : item['name']}
        editable_data = 0
        
        if ('amount' in item):
            editable_data += 1
            form_initial_data['amount'] = item['amount']

        if ('rescale_policy' in item and 'rescale_type' in item and item['rescale_type'] == "up"):
            editable_data += 1
            form_initial_data['rescale_policy'] = item['rescale_policy']

        rule_words = item['rule_readable'].split(" ")
        if ('events.scale.down' in rule_words):
            index = rule_words.index("events.scale.down")
            value = rule_words[index + 2]
            editable_data += 1
            form_initial_data['down_events_required'] = value

        if ('events.scale.up' in rule_words):
            index = rule_words.index("events.scale.up")
            value = rule_words[index + 2]
            editable_data += 1
            form_initial_data['up_events_required'] = value

        ruleForm=RuleForm(initial = form_initial_data)

        if ('amount' not in item):
            ruleForm.helper['amount'].update_attributes(type="hidden")

        if (not ('rescale_policy' in item and 'rescale_type' in item and item['rescale_type'] == "up")):
            ruleForm.helper['rescale_policy'].update_attributes(type="hidden")

        if ('events.scale.down' not in rule_words):
            ruleForm.helper['down_events_required'].update_attributes(type="hidden")

        if ('events.scale.up' not in rule_words):    
            ruleForm.helper['up_events_required'].update_attributes(type="hidden")

        item['form'] = ruleForm
        item['editable_data'] = editable_data

    rulesResources = getRulesResources(data_json)
    ruleTypes = ['requests','events','']

    config_errors = checkInvalidConfig()

    return render(request, 'rules.html', {'data': data_json, 'resources':rulesResources, 'types':ruleTypes, 'config_errors': config_errors, 'requests_errors': requests_errors, 'requests_successes': requests_successes, 'requests_info': requests_info})

def getRulesResources(data):
    resources = []
    
    for item in data:
        if (item['resource'] not in resources):
            resources.append(item['resource'])
    
    return resources

def jsonBooleanToHumanReadable(jsonBoolExpr):

    boolString = ""

    boolOperators = ['and','or','==','>=','<=','<','>','+','-','*','/']

    ## Check if dict or literal
    if type(jsonBoolExpr) is dict:
        firstElement = list(jsonBoolExpr.keys())[0]
    else:
        firstElement = jsonBoolExpr


    if (firstElement in boolOperators):
        ## Got bool expression
        jsonBoolValues = jsonBoolExpr[firstElement]
        for i in range(0, len(jsonBoolValues)):
            boolString += jsonBooleanToHumanReadable(jsonBoolValues[i])
            if (i < len(jsonBoolValues) - 1):
                boolString += " " + firstElement.upper() + " "

    elif (firstElement == 'var'):
        ## Got variable
        boolString = jsonBoolExpr[firstElement]

    else:
        ## Got literal
        boolString = str(firstElement)      

    return boolString

def rule_switch(request,rule_name):

    state = request.POST['rule_switch']

    ## send put to stateDatabase
    url = base_url + "/rule/" + rule_name 

    if (state == "rule_off"):
        url += "/deactivate"
    else:
        url += "/activate"

    req = urllib.request.Request(url, method='PUT')

    try:
        response = urllib.request.urlopen(req)
    except:
        pass

    return redirect('rules')

def processRulesPost(request, url, rule_name, field, field_put_url):

    full_url = url + rule_name + "/" + field_put_url
    headers = {'Content-Type': 'application/json'}

    new_value = request.POST[field]

    r = ""
    if (new_value != ''):
        put_field_data = {'value': new_value}

        if (field_put_url == "events_required"):
            if (field == "up_events_required"):
                event_type = "up"
            else:
                event_type = "down"

            put_field_data['event_type'] = event_type
                
        r = requests.put(full_url, data=json.dumps(put_field_data), headers=headers)

    error = ""
    if (r != "" and r.status_code != requests.codes.ok):
        soup = BeautifulSoup(r.text, features="html.parser")
        error = "Error submiting " + field + " for rule " + rule_name + ": " + soup.get_text().strip()

    return error

## Check Invalid Config
def checkInvalidConfig():
    error_lines = []

    config_path = "../../vars/main.yml"
    with open(config_path, "r") as config_file:
        config = yaml.load(config_file, Loader=yaml.FullLoader)

    serverless_containers_path = config['installation_path'] + "/ServerlessContainers"

    full_path = serverless_containers_path + "/sanity_checker.log"

    with open(full_path, 'r') as file:
        lines = file.readlines()
        lines.reverse()

    ## find last "Sanity checked" message
    i = 0
    while (i < len(lines) and "Sanity checked" not in lines[i]):
        i+= 1

    ## find last "Checking for invalid configuration" message
    i += 1
    while (i < len(lines) and "Checking for invalid configuration" not in lines[i]):
        error_lines.append(lines[i])
        i += 1

    error_lines.reverse()

    return error_lines
