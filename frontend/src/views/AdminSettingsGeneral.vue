<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import HomePageSection from '@/components/admin/HomePageSection.vue'
import SiteUrlSection from '@/components/admin/SiteUrlSection.vue'
import SectionQuickNav, {
  type QuickNavSection,
} from '@/components/SectionQuickNav.vue'
import { useScrollSpy } from '@/composables/useScrollSpy'

const { t } = useI18n()

// Add new small admin-editable settings as `<section id="...">`
// blocks below + a matching entry in this list. The quick-nav rail
// and scroll-spy pick them up automatically.
const sections = computed<QuickNavSection[]>(() => [
  { id: 'site-url', labelKey: 'admin_site_url.title' },
  { id: 'home-page', labelKey: 'admin_home_page.title' },
])

const sectionIds = computed(() => sections.value.map((s) => s.id))
const { active, lockTo } = useScrollSpy(() => sectionIds.value, {
  topOffsetPx: 80,
  bottomOffsetVh: 60,
})

function jumpTo(id: string) {
  lockTo(id)
  document.getElementById(id)?.scrollIntoView({
    behavior: 'smooth',
    block: 'start',
  })
}
</script>

<template>
  <div class="general-layout" data-density="operator">
    <div class="general-prose">
      <span class="fh-eyebrow">
        {{ t('admin_settings.eyebrow') }} / {{ t('admin_general.title') }}
      </span>

      <p class="fh-field-help intro">{{ t('admin_general.intro') }}</p>

      <hr class="fh-rule" />

      <section id="site-url" class="settings-section">
        <h2 class="settings-h2">{{ t('admin_site_url.title') }}</h2>
        <SiteUrlSection />
      </section>

      <section id="home-page" class="settings-section">
        <h2 class="settings-h2">{{ t('admin_home_page.title') }}</h2>
        <HomePageSection />
      </section>
    </div>

    <aside class="general-quicknav-rail">
      <SectionQuickNav
        :sections="sections"
        :active="active"
        :aria-label="t('admin_general.quicknav.aria')"
        @jump="jumpTo"
      />
    </aside>
  </div>
</template>

<style scoped>
.general-layout {
  display: grid;
  grid-template-columns: minmax(0, 38rem) 12rem;
  gap: var(--fh-space-6);
  align-items: start;
  max-width: 56rem;
  margin: 0 auto;
}

.general-prose {
  margin: 0;
}

.intro {
  margin: var(--fh-space-2) 0 var(--fh-space-3);
  max-width: 64ch;
}

.general-quicknav-rail {
  position: sticky;
  top: calc(var(--fh-app-header-height) + var(--fh-space-3));
  /* Align the rail with the first section heading rather than the
     page eyebrow above it. */
  padding-top: var(--fh-space-5);
}

@media (max-width: 920px) {
  .general-layout {
    grid-template-columns: minmax(0, 38rem);
    max-width: 38rem;
  }
  .general-quicknav-rail {
    display: none;
  }
}

.settings-section,
.settings-anchor {
  /* Keep the heading clear of the sticky AppHeader after click-scroll. */
  scroll-margin-top: calc(var(--fh-app-header-height) + var(--fh-space-3));
  margin-top: var(--fh-space-5);
}

.settings-section:first-of-type {
  margin-top: var(--fh-space-4);
}

.settings-h2 {
  font-family: var(--fh-font-display);
  font-size: 1.5rem;
  font-weight: 400;
  letter-spacing: -0.01em;
  margin: 0 0 var(--fh-space-3);
  color: var(--fh-ink);
}
</style>
