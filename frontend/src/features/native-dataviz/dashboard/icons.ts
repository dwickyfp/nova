import {
  Activity, ArrowDown, ArrowDownRight, ArrowLeft, ArrowRight, ArrowUp, ArrowUpRight, Award,
  Banknote, Briefcase, Building2, Calendar, Car, ChartBar, ChartColumn, ChartLine,
  ChartPie, CircleAlert, CircleCheck, CircleX, Clock, Cloud, CreditCard, Database,
  DollarSign, Droplet, Factory, Flag, Fuel, Gauge, Globe, GraduationCap,
  Heart, HeartPulse, House, Info, Leaf, Lightbulb, Lock, Mail,
  Map, MapPin, MessageSquare, Package, Percent, Phone, Plane, Server,
  ShieldCheck, Ship, ShoppingBag, ShoppingCart, Sparkles, Star, Store, Sun,
  Target, ThumbsDown, ThumbsUp, Timer, TrendingDown, TrendingUp, TriangleAlert, Trophy,
  Truck, User, UserCheck, Users, Wallet, Warehouse, Zap,
  type LucideIcon,
} from 'lucide-react'
import type { IconElement } from '../contracts'

/** The icons a dashboard may use, by their saved name. The contract allows exactly these. */
export const ICONS: Record<IconElement['icon'], LucideIcon> = {
  activity: Activity, 'arrow-down': ArrowDown, 'arrow-down-right': ArrowDownRight, 'arrow-left': ArrowLeft,
  'arrow-right': ArrowRight, 'arrow-up': ArrowUp, 'arrow-up-right': ArrowUpRight, award: Award,
  banknote: Banknote, briefcase: Briefcase, 'building-2': Building2, calendar: Calendar,
  car: Car, 'chart-bar': ChartBar, 'chart-column': ChartColumn, 'chart-line': ChartLine,
  'chart-pie': ChartPie, 'circle-alert': CircleAlert, 'circle-check': CircleCheck, 'circle-x': CircleX,
  clock: Clock, cloud: Cloud, 'credit-card': CreditCard, database: Database,
  'dollar-sign': DollarSign, droplet: Droplet, factory: Factory, flag: Flag,
  fuel: Fuel, gauge: Gauge, globe: Globe, 'graduation-cap': GraduationCap,
  heart: Heart, 'heart-pulse': HeartPulse, house: House, info: Info,
  leaf: Leaf, lightbulb: Lightbulb, lock: Lock, mail: Mail,
  map: Map, 'map-pin': MapPin, 'message-square': MessageSquare, package: Package,
  percent: Percent, phone: Phone, plane: Plane, server: Server,
  'shield-check': ShieldCheck, ship: Ship, 'shopping-bag': ShoppingBag, 'shopping-cart': ShoppingCart,
  sparkles: Sparkles, star: Star, store: Store, sun: Sun,
  target: Target, 'thumbs-down': ThumbsDown, 'thumbs-up': ThumbsUp, timer: Timer,
  'trending-down': TrendingDown, 'trending-up': TrendingUp, 'triangle-alert': TriangleAlert, trophy: Trophy,
  truck: Truck, user: User, 'user-check': UserCheck, users: Users,
  wallet: Wallet, warehouse: Warehouse, zap: Zap,
}
