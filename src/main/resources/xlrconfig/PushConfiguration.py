import xlrconfig
reload(xlrconfig)
from xlrconfig import push_configuration

executedActions = push_configuration(server, username, password, pushConfiguration, dryRun)
