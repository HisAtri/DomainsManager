import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const config: Config = {
  title: 'DomainsManager',
  tagline: 'DomainsManager 使用文档',
  favicon: 'img/logo.svg',
  future: {
    v4: true,
  },
  url: 'https://hisatri.github.io',
  baseUrl: '/',
  organizationName: 'DomainsManager',
  projectName: 'DomainsManager',
  onBrokenLinks: 'throw',
  i18n: {
    defaultLocale: 'zh-Hans',
    locales: ['zh-Hans'],
  },
  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          routeBasePath: '/',
        },
        blog: {
          showReadingTime: true,
        },
      } satisfies Preset.Options,
    ],
  ],
  themeConfig: {
    colorMode: {
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: 'DomainsManager',
      logo: {
        alt: 'DomainsManager Logo',
        src: 'img/logo.svg',
      },
      items: [
        {
          to: '/',
          position: 'left',
          label: '文档',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            {
              label: '项目介绍',
              to: '/',
            },
            {
              label: '开始使用',
              to: '/getting-started/installation',
            },
            {
              label: '使用指南',
              to: '/guides/overview',
            },
            {
              label: '项目 Wiki',
              to: '/wiki/overview',
            },
          ],
        },
        {
          title: 'Contact',
          items: [
            {
              label: 'GitHub',
              href: 'https://github.com/HisAtri/DomainsManager',
            },
            {
              label: '提交问题',
              href: 'https://github.com/HisAtri/DomainsManager/issues',
            },
          ],
        },
        {
          title: 'More',
          items: [
            {
              label: '博客',
              to: '/blog',
            },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} DomainsManager.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
