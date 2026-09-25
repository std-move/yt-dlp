#!/usr/bin/env python3

# Allow direct execution
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


import copy

from test.helper import FakeYDL
from yt_dlp.audio_languages import ffmpeg_audio_stream_args, language_key

TEST_URL = 'http://localhost/sample.mp4'


class YDL(FakeYDL):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.downloaded_info_dicts = []
        self.msgs = []

    def process_info(self, info_dict):
        self.downloaded_info_dicts.append(info_dict.copy())

    def to_screen(self, msg, *args, **kwargs):
        self.msgs.append(msg)

    def dl(self, *args, **kwargs):
        assert False, 'Downloader must not be invoked for test_audio_languages'


def _video(format_id, height, **kwargs):
    return {
        'format_id': format_id, 'ext': 'mp4', 'vcodec': 'avc1', 'acodec': 'none',
        'height': height, 'url': TEST_URL, **kwargs}


def _audio(format_id, language, abr, **kwargs):
    return {
        'format_id': format_id, 'ext': 'm4a', 'vcodec': 'none', 'acodec': 'mp4a.40.2',
        'abr': abr, 'language': language, 'url': TEST_URL, **kwargs}


# Sorted by the format sorter, the best audio is de-128 (highest language_preference),
# followed by de-64, und-320, en-192, fr-128, en-96, en-desc
FORMATS = [
    _video('v720', 720),
    _video('v1080', 1080),
    _audio('de-64', 'de', 64, language_preference=10),
    _audio('de-128', 'de', 128, language_preference=10),
    _audio('en-96', 'en-US', 96),
    _audio('en-192', 'en-US', 192),
    _audio('fr-128', 'fr', 128),
    _audio('en-desc', 'en-US-desc', 128, language_preference=-10),
    _audio('und-320', None, 320),
]
TAGGED_FORMATS = [f for f in FORMATS if f.get('language')]


def _make_result(formats, **kwargs):
    res = {
        'formats': copy.deepcopy(formats),
        'id': 'testid',
        'title': 'testtitle',
        'extractor': 'testex',
        'extractor_key': 'TestEx',
        'webpage_url': 'http://example.com/watch?v=shenanigans',
    }
    res.update(**kwargs)
    return res


@patch('yt_dlp.postprocessor.ffmpeg.FFmpegMergerPP.available', True)
@patch('yt_dlp.postprocessor.ffmpeg.FFmpegMergerPP.can_merge', lambda _: True)
class TestAudioLanguages(unittest.TestCase):
    def _download(self, params, formats=FORMATS, **info):
        ydl = YDL(params)
        ydl.process_ie_result(_make_result(formats, **info))
        return [x['format_id'] for x in ydl.downloaded_info_dicts], ydl

    def test_default_enabled_for_non_youtube(self):
        downloaded, ydl = self._download({'format': None})
        self.assertEqual(downloaded, ['v1080+de-128+en-192+fr-128'])
        merged = ydl.downloaded_info_dicts[0]
        self.assertEqual(merged['ext'], 'mkv')
        self.assertEqual(
            [f['format_id'] for f in merged['requested_formats']],
            ['v1080', 'de-128', 'en-192', 'fr-128'])
        self.assertTrue(any('Selected audio tracks for languages: deu, eng, fra' in m for m in ydl.msgs))

    def test_default_disabled_for_youtube(self):
        downloaded, _ = self._download({'format': None}, extractor='youtube', extractor_key='Youtube')
        self.assertEqual(downloaded, ['v1080+de-128'])

    def test_forced_on_and_off(self):
        downloaded, _ = self._download(
            {'format': None, 'all_audio_languages': True}, extractor='youtube', extractor_key='Youtube')
        self.assertEqual(downloaded, ['v1080+de-128+en-192+fr-128'])

        downloaded, _ = self._download({'format': None, 'all_audio_languages': False})
        self.assertEqual(downloaded, ['v1080+de-128'])

    def test_selector_is_applied_per_language(self):
        # fr has no track matching the filter and is therefore skipped
        downloaded, _ = self._download({'format': 'bv+ba[abr<=100]'})
        self.assertEqual(downloaded, ['v1080+de-64+en-96'])

        # An explicit language filter is respected as well
        downloaded, _ = self._download({'format': 'bv+ba[language^=en]'})
        self.assertEqual(downloaded, ['v1080+en-192'])

    def test_site_default_track_goes_first(self):
        # Sorting by bitrate first makes en-192 the best audio, but de is the site default
        downloaded, _ = self._download({'format': 'bv+ba', 'format_sort': ['abr']}, formats=TAGGED_FORMATS)
        self.assertEqual(downloaded, ['v1080+de-128+en-192+fr-128'])

    def test_untagged_primary_track(self):
        downloaded, _ = self._download({'format': 'bv+ba', 'format_sort': ['abr']})
        self.assertEqual(downloaded, ['v1080+de-128+und-320+en-192+fr-128'])

    def test_identical_languages_are_grouped(self):
        formats = [
            _video('v1080', 1080),
            _audio('cs-64', 'cs', 64),
            _audio('cze-128', 'cze', 128),
            _audio('ces-96', 'ces-CZ', 96),
            _audio('en-192', 'en', 192),
        ]
        downloaded, ydl = self._download({'format': None}, formats=formats)
        self.assertEqual(downloaded, ['v1080+cze-128+en-192'])
        self.assertTrue(any('Selected audio tracks for languages: ces, eng' in m for m in ydl.msgs))

    def test_default_audio_language(self):
        # No language_preference is set, so no track is marked as default by the site
        formats = [
            _video('v1080', 1080),
            _audio('en-192', 'en', 192),
            _audio('cs-128', 'cs', 128),
            _audio('fr-96', 'fr', 96),
        ]
        downloaded, ydl = self._download({'format': None}, formats=formats)
        self.assertEqual(downloaded, ['v1080+cs-128+en-192+fr-96'])
        self.assertTrue(any('Selected audio tracks for languages: ces, eng, fra' in m for m in ydl.msgs))

        # ISO 639-2/B code
        downloaded, ydl = self._download(
            {'format': None}, formats=[{**f, 'language': 'cze'} if f['format_id'] == 'cs-128' else f for f in formats])
        self.assertEqual(downloaded, ['v1080+cs-128+en-192+fr-96'])
        self.assertTrue(any('Selected audio tracks for languages: ces, eng, fra' in m for m in ydl.msgs))

        for lang in ('fr', 'fra', 'fre', 'fr-FR'):
            downloaded, _ = self._download({'format': None, 'default_audio_language': lang}, formats=formats)
            self.assertEqual(downloaded, ['v1080+fr-96+en-192+cs-128'], lang)

        # Disabled or not available: the order given by the format sorting is kept
        downloaded, _ = self._download({'format': None, 'default_audio_language': ''}, formats=formats)
        self.assertEqual(downloaded, ['v1080+en-192+cs-128+fr-96'])
        downloaded, _ = self._download({'format': None, 'default_audio_language': 'de'}, formats=formats)
        self.assertEqual(downloaded, ['v1080+en-192+cs-128+fr-96'])

        # Several tracks share the highest language_preference: none of them is the site default
        tied = [{**f, 'language_preference': 10} if f['format_id'] in ('en-192', 'fr-96') else f for f in formats]
        downloaded, _ = self._download({'format': None}, formats=tied)
        self.assertEqual(downloaded, ['v1080+cs-128+en-192+fr-96'])

        # A single track with the highest language_preference counts as the site default,
        # even if its language_preference is only unset while the others are demoted
        downloaded, _ = self._download(
            {'format': None}, formats=[*formats, _audio('de-64', 'de', 64, language_preference=10)])
        self.assertEqual(downloaded, ['v1080+de-64+en-192+cs-128+fr-96'])
        demoted = [{**f, 'language_preference': -10} if f['format_id'] in ('cs-128', 'fr-96') else f for f in formats]
        downloaded, _ = self._download({'format': None}, formats=demoted)
        self.assertEqual(downloaded, ['v1080+en-192+cs-128+fr-96'])

    def test_multiple_selections(self):
        # Candidates are matched to the selected formats by position, so a language is skipped
        # for all comma-separated alternatives if any of them picks nothing for that language
        downloaded, _ = self._download({'format': 'bv+ba,bv+ba[abr<=100]'})
        self.assertEqual(downloaded, ['v1080+de-128+en-192', 'v1080+de-64+en-96'])

        downloaded, _ = self._download({'format': 'bv+ba,bv+ba[abr<=200]'})
        self.assertEqual(downloaded, ['v1080+de-128+en-192+fr-128', 'v1080+de-128+en-192+fr-128'])

    def test_not_applied_when_multiple_audio_streams_selected(self):
        downloaded, _ = self._download({'format': 'bv+ba+ba.2', 'allow_multiple_audio_streams': True})
        self.assertEqual(downloaded, ['v1080+de-128+de-64'])

    def test_not_applied_without_separate_audio(self):
        downloaded, _ = self._download({'format': 'ba'})
        self.assertEqual(downloaded, ['de-128'])

        downloaded, _ = self._download({'format': 'bv'})
        self.assertEqual(downloaded, ['v1080'])

        muxed = {
            'format_id': 'muxed', 'ext': 'mp4', 'vcodec': 'avc1', 'acodec': 'mp4a.40.2',
            'height': 360, 'language': 'de', 'url': TEST_URL}
        downloaded, _ = self._download({'format': 'b'}, formats=[*FORMATS, muxed])
        self.assertEqual(downloaded, ['muxed'])

    def test_not_applied_when_merging_is_not_possible(self):
        downloaded, _ = self._download({'format': 'bv+ba', 'outtmpl': '-'})
        self.assertEqual(downloaded, ['v1080+de-128'])

        downloaded, _ = self._download({'format': 'bv+ba'}, is_live=True)
        self.assertEqual(downloaded, ['v1080+de-128'])

        with patch('yt_dlp.postprocessor.ffmpeg.FFmpegMergerPP.available', False):
            downloaded, _ = self._download({'format': 'bv+ba'})
        self.assertEqual(downloaded, ['v1080+de-128'])

    def test_language_key(self):
        for lang, expected in [
            ('en', 'eng'), ('en-US', 'eng'), ('en_GB', 'eng'), ('EN-us-desc', 'eng'),
            ('deu', 'deu'), ('ger', 'deu'), ('cs', 'ces'), ('ces', 'ces'), ('CZE-cz', 'ces'),
            ('zh-Hans', 'zho'), ('chi', 'zho'), ('xx', 'xx'),
            ('und', None), ('zxx', None), ('', None), (None, None),
        ]:
            self.assertEqual(language_key({'language': lang}), expected, lang)
        self.assertIsNone(language_key({}))

    def test_ffmpeg_audio_stream_args(self):
        video = _video('v', 720)
        self.assertEqual(
            ffmpeg_audio_stream_args([video, _audio('a1', 'de', 128), _audio('a2', 'en-US', 128), _audio('a3', None, 128)]),
            ['-disposition:v:0', '+default',
             '-metadata:s:a:0', 'language=deu', '-disposition:a:0', '+default',
             '-metadata:s:a:1', 'language=eng', '-disposition:a:1', '-default',
             '-disposition:a:2', '-default'])
        self.assertEqual(
            ffmpeg_audio_stream_args([_audio('a1', 'de', 128), _audio('a2', 'en', 128)]),
            ['-metadata:s:a:0', 'language=deu', '-disposition:a:0', '+default',
             '-metadata:s:a:1', 'language=eng', '-disposition:a:1', '-default'])
        # Nothing is changed for merges with a single audio stream or with muxed inputs
        self.assertEqual(ffmpeg_audio_stream_args([video, _audio('a1', 'de', 128)]), [])
        self.assertEqual(ffmpeg_audio_stream_args([_audio('a1', 'de', 128)]), [])
        muxed = {'format_id': 'm', 'vcodec': 'avc1', 'acodec': 'mp4a.40.2', 'language': 'de'}
        self.assertEqual(ffmpeg_audio_stream_args([muxed, _audio('a1', 'en', 128)]), [])


if __name__ == '__main__':
    unittest.main()
