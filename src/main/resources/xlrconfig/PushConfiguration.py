import json
import xlrconfig

reload(xlrconfig)

push_config = json.loads(pushConfiguration)
executedActions = xlrconfig.push_configuration(server, username, password, push_config, dryRun)
