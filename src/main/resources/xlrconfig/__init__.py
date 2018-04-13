from com.xebialabs.xlrelease.plugin.webhook import XmlPathResult
from com.xebialabs.deployit.plumbing import CurrentVersion
from com.xebialabs.deployit import ServerConfiguration
from xlrelease.HttpRequest import HttpRequest


def push_configuration(server, username, password, push_config, dry_run):
    pusher = ConfigurationSynchronizer(server, username, password, push_config, dry_run)
    source_xlr = pusher.get_source_xlr_details()
    target_xlr = pusher.get_target_xlr_details()
    print('Going to push configuration from XL Release %s (%s) to XL Release %s (%s)' % (
        source_xlr['version'], source_xlr['url'], target_xlr['version'], target_xlr['url']
    ))

    executed_actions = {
        'yo': 'bla'
    }
    return executed_actions


# noinspection PyMethodMayBeStatic
class ConfigurationSynchronizer:

    def __init__(self, server, username, password, push_config, dry_run):
        self.xlr_server_connection_details = (server, username, password)
        self.push_config = push_config
        self.dry_run = dry_run

    def get_source_xlr_details(self):
        return {
            'url': ServerConfiguration.getInstance().getServerUrl(),
            'version': CurrentVersion.get()
        }

    def get_target_xlr_details(self):
        request = HttpRequest(*self.xlr_server_connection_details)
        response = request.get('/server/info', contentType='application/xml')
        if response.isSuccessful():
            version = XmlPathResult(response.response, '/server-info/version').get()
            return {
                'url': self.xlr_server_connection_details[0]['url'],
                'version': version
            }
        else:
            response.errorDump()
            raise Exception('Version request to /server/info failed with status %d' % response.getStatus())
