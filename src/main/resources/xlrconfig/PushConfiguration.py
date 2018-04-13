###
#<property name="server" category="input" label="Server" referenced-type="xlrconfig.XLReleaseServer" kind="ci"
#description="XL Release server to push configuration to."/>
#<property name="username" required="false" category="input"
#description="Username to use when connecting to the XL Release server."/>
#<property name="password" password="true" required="false" category="input" label="Password"
#description="Password to use when connecting to the XL Release server."/>
#<property name="configuration" default="{}" category="input" kind="string"
#description="Configuration of what entities to push from which folders, with patterns, as JSON string. Check the documentation for details of the format."/>
#<property name="dryRun" default="true" category="input" kind="boolean"
#description="If checked then this task will print out the actions that would be done, but not execute any actions."/>
#
#<property name="executedActions" default="" category="output"
#description="A report of which actions were executed (or would be executed when dryRun=true), in JSON format. Check the documentation for details of the format."/>
###

import xlrconfig

xlrconfig = reload(xlrconfig)

print('server: %s' % server.url)
print('configuration: %s' % pushConfiguration)
print('dryRun: %s' % dryRun)

executedActions = '''{"yo":"bla"}'''
