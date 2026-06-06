<script setup lang="ts">
/* The file:Heron wordmark.
 *
 * "file" + ":" + "Heron" set in Instrument Serif. The colon is the project's
 * single signature flourish - set in the warm-amber accent so it carries
 * weight without leaning on italics or color on the letters themselves.
 * Sized via the `size` prop.
 *
 * `linkable` (default true) controls whether the wordmark is a router
 * link. The authed AppHeader passes `false` when an admin disables the
 * default home page - the wordmark still renders, just without the link
 * back to `/`. */
const props = withDefaults(
  defineProps<{
    size?: 'sm' | 'md' | 'lg'
    linkable?: boolean
  }>(),
  { size: 'md', linkable: true },
)
</script>

<template>
  <RouterLink
    v-if="props.linkable"
    to="/"
    class="brand"
    :data-size="size ?? 'md'"
    aria-label="file:Heron home"
  >
    <span class="brand-fix">file</span><span class="brand-colon" aria-hidden="true">:</span><span
      class="brand-fix"
    >Heron</span>
  </RouterLink>
  <span
    v-else
    class="brand brand-static"
    :data-size="size ?? 'md'"
    aria-label="file:Heron"
  >
    <span class="brand-fix">file</span><span class="brand-colon" aria-hidden="true">:</span><span
      class="brand-fix"
    >Heron</span>
  </span>
</template>

<style scoped>
.brand {
  display: inline-flex;
  align-items: baseline;
  font-family: var(--fh-font-display);
  font-weight: 400;
  letter-spacing: -0.015em;
  color: var(--fh-ink);
  text-decoration: none;
  line-height: 1;
}

.brand:hover {
  color: var(--fh-ink);
}

.brand-static {
  /* No pointer; visually identical otherwise. */
  cursor: default;
}

.brand[data-size='sm'] {
  font-size: 1.5rem;
}
.brand[data-size='md'] {
  font-size: 2.25rem;
}
.brand[data-size='lg'] {
  font-size: 3.5rem;
}

.brand-fix {
  font-style: normal;
}

/* The signature: warm-amber colon, never italicized, slightly tightened. */
.brand-colon {
  color: var(--fh-accent);
  margin: 0 -0.04em;
  transition: color var(--fh-duration-fast) var(--fh-easing);
}

.brand:hover .brand-colon {
  color: var(--fh-ink);
}

.brand-static:hover .brand-colon {
  /* Don't shift on hover when not linkable. */
  color: var(--fh-accent);
}
</style>
