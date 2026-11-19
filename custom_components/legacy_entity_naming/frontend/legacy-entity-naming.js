/*
 * Legacy Entity Naming - frontend module.
 *
 * Since 2026.11 the dashboards build an entity's default name from its parts
 * (parent device + device + entity), so a name you typed gets the device name
 * added in front. While switch.legacy_entity_naming is on, this module makes
 * the default name the entity's friendly_name again. The integration's core
 * patch makes friendly_name follow the pre-2026.11 rules (your name exactly as
 * typed), so cards, badges, rows and more-info show the same name as before.
 *
 * Only the *default* name is changed. A name you set on a card (text, or parts
 * you picked in the card editor) is left alone.
 *
 * It wraps hass.formatEntityName where the frontend installs it
 * (<home-assistant>._updateHass), so every card and context consumer gets the
 * wrapped version. If the hook is missing (a future frontend), it does nothing.
 */
(() => {
  const TAG = "[legacy-entity-naming]";
  const PLATFORM = "legacy_entity_naming";
  const WRAPPED = Symbol.for("legacy-entity-naming.wrapped");

  // The default name item lists the 2026.11 frontend uses
  const DEFAULT_LISTS = [
    "parent_device,device,entity",
    "device,entity",
    "parent_device,device",
    "device",
  ];

  let hassRef = null;

  const isEnabled = () => {
    const hass = hassRef;
    if (!hass || !hass.entities || !hass.states) return false;
    for (const [entityId, entry] of Object.entries(hass.entities)) {
      if (entry && entry.platform === PLATFORM && entityId.startsWith("switch.")) {
        const state = hass.states[entityId];
        return !state || state.state !== "off";
      }
    }
    return false; // integration not loaded: leave HA's naming alone
  };

  const isDefaultName = (name) => {
    if (name === undefined || name === null) return true;
    if (!Array.isArray(name)) return false;
    if (name.some((item) => !item || item.type === "text")) return false;
    return DEFAULT_LISTS.includes(name.map((item) => item.type).join(","));
  };

  // "Kitchen" followed by "Kitchen Plug" -> the first part adds nothing
  const isPrefixOf = (part, next) => {
    const a = part.trim().toLocaleLowerCase();
    const b = next.trim().toLocaleLowerCase();
    return a === b || b.startsWith(a + " ");
  };

  // Parts picked on a card (area, device, entity, ...): build them one by
  // one and drop any part the following part already starts with, so an area
  // or device name is never shown twice.
  const composeWithoutRepeats = (format, self, stateObj, items, options) => {
    const parts = items
      .map((item) =>
        item.type === "text" ? item.text : format.call(self, stateObj, [item], options)
      )
      .filter((part) => typeof part === "string" && part.trim() !== "");
    const kept = parts.filter((part, i) => {
      if (i + 1 >= parts.length) return true;
      return !isPrefixOf(part, parts[i + 1]);
    });
    return kept.join(options && options.separator != null ? options.separator : " ");
  };

  const wrap = (format) => {
    if (typeof format !== "function" || format[WRAPPED]) return format;
    const wrapped = function (stateObj, name, options) {
      try {
        if (stateObj && isEnabled()) {
          if (
            isDefaultName(name) &&
            stateObj.attributes &&
            typeof stateObj.attributes.friendly_name === "string" &&
            stateObj.attributes.friendly_name !== ""
          ) {
            return stateObj.attributes.friendly_name;
          }
          const items = Array.isArray(name) ? name : name && typeof name === "object" ? [name] : null;
          if (items && items.length > 1 && items.every((item) => item && item.type)) {
            return composeWithoutRepeats(format, this, stateObj, items, options);
          }
        }
      } catch (err) {
        // Never break a card over a name
      }
      return format.call(this, stateObj, name, options);
    };
    wrapped[WRAPPED] = true;
    return wrapped;
  };

  const patch = (el) => {
    const proto = Object.getPrototypeOf(el);
    if (proto[WRAPPED]) return;
    const original = proto._updateHass;
    if (typeof original !== "function") {
      console.warn(TAG, "frontend hook not found; legacy naming inactive in dashboards");
      return;
    }
    let unwrapped = null;
    proto._updateHass = function (obj) {
      if (obj && obj.formatEntityName) {
        if (!obj.formatEntityName[WRAPPED]) unwrapped = obj.formatEntityName;
        obj = { ...obj, formatEntityName: wrap(obj.formatEntityName) };
      }
      const result = original.call(this, obj);
      hassRef = this.hass;
      return result;
    };
    proto[WRAPPED] = true;

    // Follow the switch. When it flips, install a fresh formatter function so
    // every card sees a changed formatter and re-renders its name.
    let lastEnabled = null;
    const watch = () => {
      if (el.hass) hassRef = el.hass;
      const enabled = isEnabled();
      if (lastEnabled !== null && enabled !== lastEnabled && unwrapped) {
        el._updateHass({ formatEntityName: unwrapped });
      }
      lastEnabled = enabled;
      setTimeout(watch, 1000);
    };

    // Formatter may already be installed before this module loaded
    if (el.hass && el.hass.formatEntityName && !el.hass.formatEntityName[WRAPPED]) {
      el._updateHass({ formatEntityName: el.hass.formatEntityName });
    }
    watch();
    console.info(TAG, "active");
  };

  const start = () => {
    const el = document.querySelector("home-assistant");
    if (el) {
      patch(el);
    } else {
      setTimeout(start, 100);
    }
  };

  customElements.whenDefined("home-assistant").then(start);
})();
