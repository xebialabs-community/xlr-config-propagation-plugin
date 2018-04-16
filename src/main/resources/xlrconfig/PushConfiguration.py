import json
from xlrconfig.configuration_pusher import push_configuration
# reload(xlrconfig) uncomment this for faster development cycle

push_config = json.loads(pushConfiguration)
push_result = push_configuration((server, username, password), push_config, dryRun, {
    'folderApi': folderApi,
    'templateApi': templateApi,
    'configurationApi': configurationApi
})

stats = json.dumps(push_result['stats'])
actions = json.dumps(push_result['actions'])
warnings = json.dumps(push_result['warnings'])
errors = json.dumps(push_result['errors'])
