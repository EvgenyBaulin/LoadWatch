// Регистрация только нужных частей Chart.js (меньше бандл) и адаптера дат.

import {
  BarController,
  BarElement,
  CategoryScale,
  Chart,
  Filler,
  Legend,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  TimeScale,
  Tooltip,
} from 'chart.js'
import 'chartjs-adapter-date-fns'
import { marksPlugin } from './annotations'

Chart.register(
  BarController,
  BarElement,
  CategoryScale,
  Filler,
  Legend,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  TimeScale,
  Tooltip,
  marksPlugin,
)

Chart.defaults.animation = false
Chart.defaults.maintainAspectRatio = false
