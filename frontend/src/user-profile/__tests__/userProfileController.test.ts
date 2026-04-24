import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import UserProfileController from "../UserProfileController.vue";

describe("UserProfileController", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    document.body.innerHTML = "";
    localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    document.body.innerHTML = "";
    localStorage.clear();
  });

  it("removes the recommended alert when it was previously dismissed", () => {
    localStorage.setItem("account_setup_recommended_v1", "1");

    const alert = document.createElement("div");
    alert.id = "account-setup-recommended-alert";
    alert.setAttribute("data-dismiss-key", "account_setup_recommended_v1");
    document.body.appendChild(alert);

    mount(UserProfileController);

    expect(document.getElementById("account-setup-recommended-alert")).toBeNull();
  });

  it("updates the displayed timezone clock", () => {
    const tzNode = document.createElement("div");
    tzNode.id = "user-timezone";
    tzNode.setAttribute("data-timezone", "UTC");
    document.body.appendChild(tzNode);

    const timeNode = document.createElement("div");
    timeNode.id = "user-time";
    timeNode.textContent = "initial";
    document.body.appendChild(timeNode);

    mount(UserProfileController);

    vi.advanceTimersByTime(1100);

    expect(timeNode.textContent).not.toBe("initial");
  });
});