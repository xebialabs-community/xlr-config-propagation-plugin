from com.xebialabs.xlrelease.plugin.webhook import XmlPathResult
from xlrelease.HttpRequest import HttpRequest
import json
import urllib
import requests
from zipfile import ZipFile
import sys


class RemoteXlr:
    def __init__(self, server, username, password):
        self.server = server
        self.username = username
        self.password = password
        self.folder_path_to_id_cache = {}
        self.configuration_type_title_to_id_cache = {}
        self.folder_id_to_template_title_to_id_cache = {}

    def get_xlr_details(self):
        response = self._request().get('/server/info', contentType='application/xml')
        if response.isSuccessful():
            version = XmlPathResult(response.response, '/server-info/version').get()
            return {
                'url': self.server['url'],
                'version': version
            }
        else:
            raise Exception('Version request to /server/info failed with status %d, response: %s' %
                            (response.getStatus(), response.response))

    def get_folder_id_by_path(self, path):
        if path in self.folder_path_to_id_cache:
            return self.folder_path_to_id_cache[path]
        query = '?byPath=%s' % urllib.quote(path)
        response = self._request().get('/api/v1/folders/find' + query, contentType='application/json')
        if response.isSuccessful():
            folder_id = json.loads(response.response)['id']
        elif response.getStatus() == 404:
            folder_id = None
        else:
            raise Exception('Request to find a folder [%s] failed with status %d, response: %s' %
                            (path, response.getStatus(), response.response))
        self.folder_path_to_id_cache[path] = folder_id
        return folder_id

    def get_configuration_id_by_type_and_title(self, config_type, config_title, warnings):
        path = '%s/%s' % (config_type, config_title)
        if path in self.configuration_type_title_to_id_cache:
            return self.configuration_type_title_to_id_cache[path]
        query = '?configurationType=%s&title=%s' % (urllib.quote(config_type), urllib.quote(config_title))
        response = self._request().get('/api/v1/config/byTypeAndTitle' + query, contentType='application/json')
        if response.isSuccessful():
            configurations = json.loads(response.response)
            if not configurations:
                configuration_id = None
            else:
                if len(configurations) > 1:
                    warnings.append('Found %d configurations by type [%s] and title [%s], choosing the first from: %s' %
                                    (len(configurations), config_type, config_title, [c['id'] for c in configurations]))
                configuration_id = configurations[0]['id']
        else:
            raise Exception('Request to find a configuration [%s/%s] failed with status %d, response: %s' %
                            (config_type, config_title, response.getStatus(), response.response))
        self.configuration_type_title_to_id_cache[path] = configuration_id
        return configuration_id

    def get_template_id_by_folder_and_title(self, folder_id, title, warnings):
        if folder_id in self.folder_id_to_template_title_to_id_cache:
            return self.folder_id_to_template_title_to_id_cache[folder_id].get(title, None)

        # Unfortunately there's no public API to search for a template by folder _and_ title,
        # so iterate through all templates of a folder and cache them
        template_titles_to_ids = {}
        context = '/api/v1/folders/%s/templates' % folder_id
        results_per_page = 20
        page = 0
        while True:
            query = '?page=%d&resultsPerPage=%d&depth=1' % (page, results_per_page)
            response = self._request().get(context + query, contentType='application/json')
            if response.isSuccessful():
                templates = json.loads(response.response)
            else:
                raise Exception('Request to get page %d of templates of folder [%s] failed with status %d, response: %s'
                                % (page, folder_id, response.getStatus(), response.response))
            if not templates:
                # pagination finished
                break
            for template in templates:
                if template['title'] in template_titles_to_ids:
                    warnings.append('Found more than one template by title [%s] in remote folder [%s], choosing '
                                    'the first one: [%s]' % (template['title'], folder_id,
                                                             template_titles_to_ids[template['title']]))
                else:
                    template_titles_to_ids[template['title']] = template['id']
            page += 1

        self.folder_id_to_template_title_to_id_cache[folder_id] = template_titles_to_ids
        return self.folder_id_to_template_title_to_id_cache[folder_id].get(title, None)

    def import_template(self, folder_id, template_json, template_path, warnings):
        if folder_id == 'Applications':
            query = ''
        else:
            query = '?folderId=%s' % folder_id

        manifest = {"xlr-data-model-version":"8.5.0#1","xlr-version":"8.5.1"}

        releaseTemplate = json.loads(template_json)

        with open('manifest.json', 'w') as manifest_file:
             json.dump(manifest, manifest_file)
        with open('release-template.json', 'w') as release_file:
            json.dump(releaseTemplate, release_file)

        # create a ZipFile object
        zipObj = ZipFile('template.xlr', 'w')

        # Add multiple files to the zip
        zipObj.write('manifest.json')
        zipObj.write('release-template.json')

        # close the Zip File
        zipObj.close()

        XLRfile = open('template.xlr', 'rb')

        # set XLR credentials
        XLR_username = self.server['username']
        XLR_password = self.server['password']

        headers = {"contentType":"multipart/form-data"}

        url = str(self.server['url'])+'/api/v1/templates/import'+ query

        response = requests.post(url, files={'file': XLRfile.read()}, auth=(XLR_username,XLR_password), headers=headers)

        print("response.status_code == " + str(response.status_code) + "\n")
        if response.status_code == 200:
            import_result = json.loads(response.content)[0]  # there's always exactly one result
            import_warnings = filter(lambda w: not w.startswith('Teams in this template have been removed.'),import_result.get('warnings', []))
            if import_warnings:
                warnings.append('Got following warnings when importing template [%s]: %s' %
                                (template_path, import_warnings))
            internal_id = import_result['id']
            # the import result ID is in internal API format: "Folder1-Release1"
            public_id = 'Applications/%s' % (internal_id.replace('-', '/'))

            return public_id
        else:
            raise Exception('Request to import template [%s] failed with status %d, response: [%s]. ''Check the log files for more details' % (template_path, response.status_code, response.content))

    def _request(self):
        return HttpRequest(self.server, self.username, self.password)
