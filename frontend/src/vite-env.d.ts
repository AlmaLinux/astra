/// <reference types="vite/client" />

declare module "*.vue" {
  import type { DefineComponent } from "vue";

  const component: DefineComponent<Record<string, never>, Record<string, never>, unknown>;
  export default component;
}

// Chart.js is loaded as a global static asset (not bundled), so we declare
// a minimal interface here instead of importing from the npm package.
interface ChartDataset {
  label?: string;
  data: number[];
  backgroundColor?: string | string[];
  borderColor?: string | string[];
  borderWidth?: number;
  fill?: boolean;
  tension?: number;
}

interface ChartData {
  labels?: string[];
  datasets: ChartDataset[];
}

interface ChartOptions {
  responsive?: boolean;
  maintainAspectRatio?: boolean;
  scales?: Record<string, unknown>;
  plugins?: Record<string, unknown>;
  indexAxis?: string;
}

interface ChartConfiguration {
  type: string;
  data: ChartData;
  options?: ChartOptions;
}

interface ChartInstance {
  data: ChartData;
  update(): void;
  destroy(): void;
}

interface ChartConstructor {
  new (canvas: HTMLCanvasElement, config: ChartConfiguration): ChartInstance;
}

declare global {
  interface Window {
    Chart: ChartConstructor | { Chart: ChartConstructor };
  }
}
