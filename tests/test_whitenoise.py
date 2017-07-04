import hashlib
import base64
import os
import shutil
import tempfile
from unittest import TestCase
from wsgiref.simple_server import demo_app

from .utils import TestServer, Files

from whitenoise import WhiteNoise, HashedEtagWhiteNoise


# Update Py2 TestCase to support Py3 method names
if not hasattr(TestCase, 'assertRegex'):
    class Py3TestCase(TestCase):
        def assertRegex(self, *args, **kwargs):
            return self.assertRegexpMatches(*args, **kwargs)
    TestCase = Py3TestCase


class WhiteNoiseTest(TestCase):

    @classmethod
    def setUpClass(cls):
        cls.files = cls.init_files()
        cls.application = cls.init_application(root=cls.files.directory)
        cls.server = TestServer(cls.application)
        super(WhiteNoiseTest, cls).setUpClass()

    @staticmethod
    def init_files():
        return Files('assets',
                     js='subdir/javascript.js',
                     gzip='compressed.css',
                     gzipped='compressed.css.gz',
                     custom_mime='custom-mime.foobar')

    @staticmethod
    def init_application(**kwargs):
        def custom_headers(headers, path, url):
            if url.endswith('.css'):
                headers['X-Is-Css-File'] = 'True'
        kwargs.update(max_age=1000,
                      mimetypes={'.foobar': 'application/x-foo-bar'},
                      add_headers_function=custom_headers)
        return WhiteNoise(demo_app, **kwargs)

    def test_get_file(self):
        response = self.server.get(self.files.js_url)
        self.assertEqual(response.content, self.files.js_content)
        self.assertRegex(response.headers['Content-Type'], r'application/javascript\b')
        self.assertRegex(response.headers['Content-Type'], r'.*\bcharset="utf-8"')

    def test_get_not_accept_gzip(self):
        response = self.server.get(self.files.gzip_url, headers={'Accept-Encoding': ''})
        self.assertEqual(response.content, self.files.gzip_content)
        self.assertEqual(response.headers.get('Content-Encoding', ''), '')
        self.assertEqual(response.headers['Vary'], 'Accept-Encoding')

    def test_get_accept_gzip(self):
        response = self.server.get(self.files.gzip_url)
        self.assertEqual(response.content, self.files.gzip_content)
        self.assertEqual(response.headers['Content-Encoding'], 'gzip')
        self.assertEqual(response.headers['Vary'], 'Accept-Encoding')

    def test_not_modified_exact(self):
        response = self.server.get(self.files.js_url)
        last_mod = response.headers['Last-Modified']
        response = self.server.get(self.files.js_url, headers={'If-Modified-Since': last_mod})
        self.assertEqual(response.status_code, 304)

    def test_not_modified_future(self):
        last_mod = 'Fri, 11 Apr 2100 11:47:06 GMT'
        response = self.server.get(self.files.js_url, headers={'If-Modified-Since': last_mod})
        self.assertEqual(response.status_code, 304)

    def test_modified(self):
        last_mod = 'Fri, 11 Apr 2001 11:47:06 GMT'
        response = self.server.get(self.files.js_url, headers={'If-Modified-Since': last_mod})
        self.assertEqual(response.status_code, 200)

    def test_max_age(self):
        response = self.server.get(self.files.js_url)
        self.assertEqual(response.headers['Cache-Control'], 'max-age=1000, public')

    def test_other_requests_passed_through(self):
        response = self.server.get('/not/static')
        self.assertIn('Hello world!', response.text)

    def test_non_ascii_requests_safely_ignored(self):
        response = self.server.get(u"/\u263A")
        self.assertIn('Hello world!', response.text)

    def test_add_under_prefix(self):
        prefix = '/prefix'
        self.application.add_files(self.files.directory, prefix=prefix)
        response = self.server.get(prefix + self.files.js_url)
        self.assertEqual(response.content, self.files.js_content)

    def test_response_has_allow_origin_header(self):
        response = self.server.get(self.files.js_url)
        self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), '*')

    def test_response_has_correct_content_length_header(self):
        response = self.server.get(self.files.js_url)
        length = int(response.headers['Content-Length'])
        self.assertEqual(length, len(self.files.js_content))

    def test_gzip_response_has_correct_content_length_header(self):
        response = self.server.get(self.files.gzip_url)
        length = int(response.headers['Content-Length'])
        self.assertEqual(length, len(self.files.gzipped_content))

    def test_post_request_returns_405(self):
        response = self.server.request('post', self.files.js_url)
        self.assertEqual(response.status_code, 405)

    def test_head_request_has_no_body(self):
        response = self.server.request('head', self.files.js_url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.content)

    def test_custom_mimetype(self):
        response = self.server.get(self.files.custom_mime_url)
        self.assertRegex(response.headers['Content-Type'], r'application/x-foo-bar\b')

    def test_custom_headers(self):
        response = self.server.get(self.files.gzip_url)
        self.assertEqual(response.headers['x-is-css-file'], 'True')


class WhiteNoiseAutorefresh(WhiteNoiseTest):

    @classmethod
    def setUpClass(cls):
        cls.files = cls.init_files()
        cls.tmp = tempfile.mkdtemp()
        cls.application = cls.init_application(root=cls.tmp, autorefresh=True)
        cls.server = TestServer(cls.application)
        # Copy in the files *after* initializing server
        copytree(cls.files.directory, cls.tmp)
        super(WhiteNoiseTest, cls).setUpClass()

    def test_no_error_on_very_long_filename(self):
        response = self.server.get('/blah' * 1000)
        self.assertNotEqual(response.status_code, 500)

    @classmethod
    def tearDownClass(cls):
        super(WhiteNoiseTest, cls).tearDownClass()
        # Remove temporary directory
        shutil.rmtree(cls.tmp)


def copytree(src, dst):
    for name in os.listdir(src):
        src_path = os.path.join(src, name)
        dst_path = os.path.join(dst, name)
        if os.path.isdir(src_path):
            shutil.copytree(src_path, dst_path)
        else:
            shutil.copy2(src_path, dst_path)


class HashedETagTest(WhiteNoiseTest):

    @classmethod
    def setUpClass(cls):
        super(HashedETagTest, cls).setUpClass()
        cls.application = HashedEtagWhiteNoise(demo_app, root=cls.files.directory)
        cls.server = TestServer(cls.application)

    @classmethod
    def init_files(cls):
        cls.files = super(HashedEtagWhiteNoise, cls).init_files()
        hasher = hashlib.md5()
        for f in cls.files:
            with open(f.plain_file[0], 'rb') as fp:
                f.data = fp.read()
            f.hash = hasher.update(f.data)

    def test_200s(self):
        for file in self.files + self.immutable_files:
            self._assert_200(**file)
            self._assert_200(if_none_match_header='invalid', **file)
            self._assert_200(if_none_match_header='"invalid"', **file)
            self._assert_200(if_none_match_header='W/"randomstuff"', **file)
            self._assert_200(if_none_match_header='W/,,"random,stuff",',
                             **file)

    def test_304_ordinary(self):
        for file in self.files + self.immutable_files:
            self._assert_304(if_none_match_header=file["etag"], **file)

    def test_304_weak(self):
        for file in self.files:
            self._assert_304(if_none_match_header='W/{:s}'.format({file["etag"]}), **file)

    def test_304_list_last(self):
        for file in self.files:
            self._assert_304(
                if_none_match_header='"randomvalue", "somevalue", {file["etag"]}',
                **file)

    def test_304_list_middle(self):
        for file in self.files:
            self._assert_304(
                if_none_match_header='"something","weid", "breakmyparser",{:s}, "random", "whatever"'.format(
                    file["etag"]), **file)

    def test_304_list_first(self):
        for file in self.files:
            self._assert_304(
                if_none_match_header='{file["etag"]}, "random","oops", "whatever"',
                **file)

    def test_304_weak(self):
        for file in self.files:
            self._assert_304(
                if_none_match_header='W/"randomvalue", "somevalue", {file["etag"]} ',
                **file)

    def test_304_dangling_comma(self):
        for file in self.files:
            self._assert_304(
                    if_none_match_header='W/"randomvalue", "somevalue", {:s}, what,'.format(
                        file['etag']),
                    **file)

    def _assert_200(self, filename, etag, data, if_none_match_header=None):
        response = self.server.get(
                "/{:s}".format(filename),
                headers={"If-None-Match": if_none_match_header}
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(data, response.content)
        self.assertEqual(
            response.headers['Etag'],
            etag)

    def _assert_304(self, filename, if_none_match_header, etag, data=None):
        response = self.server.get(
                "/{:s}".format(filename),
                headers={"If-None-Match": if_none_match_header}
        )
        self.assertEqual(304, response.status_code)
        self.assertEqual(response.content, b"")
        self.assertEqual(response.headers["ETag"], etag)
