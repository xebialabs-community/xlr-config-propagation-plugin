from com.xebialabs.deployit.security import Permissions
import xlrconfig

reload(xlrconfig)

if Permissions.getAuthenticatedUserName() != 'admin':
    raise Exception('This endpoint is used for testing purposes and can only be invoked by user [admin], '
                    'but current user is [%s]' % Permissions.getAuthenticatedUserName())


def to_dict(http_configuration):
    return {
        'url': http_configuration.getUrl(),
        'authenticationMethod': http_configuration.getAuthenticationMethod(),
        'username': http_configuration.getUsername(),
        'password': http_configuration.getPassword(),
        'domain': http_configuration.getDomain(),
        'proxyHost': http_configuration.getProxyHost(),
        'proxyPort': http_configuration.getProxyPort(),
        'proxyUsername': http_configuration.getProxyUsername(),
        'proxyPassword': http_configuration.getProxyPassword()
    }


xlr_server_name = request.query.get('targetXlrName')
dry_run = request.query.get('dryRun', '').lower() == 'true'
push_config = request.entity

xlr_server = next(iter(configurationApi.searchByTypeAndTitle('xlrconfig.XLReleaseServer', xlr_server_name)), None) \
    if xlr_server_name else None


if xlr_server and push_config:

    logger.info('Processing the following spec to target XL Release [%s], dry run = %s: %s' %
                (xlr_server_name, dry_run, push_config))

    # Get the passwords and convert to a dict, as that's expected by the underlying HttpRequest
    xlr_server = securityApi.decrypt(configurationApi.getConfiguration(xlr_server.getId()))
    xlr_server = to_dict(xlr_server)

    executed_actions = xlrconfig.push_configuration((xlr_server, None, None), push_config, dry_run, {
        'folderApi': folderApi,
        'templateApi': templateApi,
        'configurationApi': configurationApi
    })

    logger.info('Finished pushing the configurations: %s' % executed_actions)

    response.entity = executed_actions

else:
    response.statusCode = 400
    if not xlr_server_name:
        response.entity = 'Missing required query parameter "targetXlrName" with the name of xlrconfig.XLReleaseServer'
    elif not xlr_server:
        response.entity = 'Cannot find configuration by type [xlrconfig.XLReleaseServer] and title [%s]' \
                          % xlr_server_name
    elif not push_config:
        response.entity = 'Missing POST body with the JSON specification of what needs to be pushed'